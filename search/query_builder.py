"""
search/query_builder.py
Побудова текстів запитів, вікна, квоти, драбина пріоритету та
дедуплікація кандидатів. Специфікація — PLAN_SEARCH.md, §13 та §14.

Крок 3 (§22) реалізує наскрізний тонкий зріз лише для каналу A: пошук
вікна 6–10 слів (§13, кроки 1–7) з балами й обрізанням до 220 символів,
побудову `pdf_anchor` (спрощену — повна евристика §15 переваги рідкісних
слів/прізвищ/чисел належить кроку 10) і збірку `SearchQuery`/`SearchResult`.
Квоти, посекційна дедуплікація, драбина A/N/B/K → T → L і `SectionShortfall`
— крок 11. Виробничі детектори A/N/B/K/T/L уже живуть у `search/markers.py`,
але тексти запитів нових каналів і provenance належать кроку 10. Тому цей
модуль поки будує лише A-запити, а нульові лічильники інших каналів є
видимою діагностикою незавершеної інтеграції.

Сигнали каналу A обчислюються й потрапляють у `signal_hits` для КОЖНОГО
речення незалежно від типу розділу (§6.1: "сигнали і кандидати UNKNOWN
лишаються в діагностиці"). Обмеження `CONTENT_SECTION_KINDS` застосовується
лише нижче за течією — до перетворення кандидата на `SearchQuery`; відсів
за типом розділу має окрему причину для `UNKNOWN` (`section_unknown`) і
окрему для `TITLE`/`TOC`/`ABSTRACT`/`BIBLIO`/`APPENDIX`
(`section_not_content_kind`) у `rejected_by_reason`.
"""

from __future__ import annotations

import hashlib
import re

from search import ALGO_VERSION
from search.calques import DICT_VERSION
from search.markers import STOPWORDS, find_channel_a_signals, normative_marker_ids, score_channel_a
from search.normalization import map_normalized_offsets, normalize_text, tokenize
from search.types import (
    CONTENT_SECTION_KINDS,
    CalqueMetrics,
    CandidateMetrics,
    Channel,
    DedupMetrics,
    Language,
    QueryPart,
    QueryPartOrigin,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchQuery,
    SearchResult,
    SearchToken,
    SectionKind,
    SentenceDonor,
    SignalHit,
    SourceSpan,
    TextZone,
)

WINDOW_MIN_WORDS = 6
WINDOW_MAX_WORDS = 10
MAX_QUERY_CHARS = 220
ANCHOR_MIN_WORDS = 8
ANCHOR_MAX_WORDS = 15

SIGNAL_COVERAGE_BONUS = 4.0
PROPER_NAME_BONUS = 3.0
NUMBER_DATE_BONUS = 3.0
RARE_FORM_BONUS = 2.0
LONG_WORD_BONUS = 1.0
LONG_WORD_MIN_LEN = 6
NORMATIVE_PENALTY = 2.0

MAIN_SELECTION_THRESHOLD = 4.0

_STOPWORDS = STOPWORDS

_UK_ONLY_CHARS = set("іїєґІЇЄҐ")
_RU_ONLY_CHARS = set("ыэъёЫЭЪЁ")


def build_search_result(document: SearchDocument) -> SearchResult:
    """Тонкий зріз конвеєра: канал A → `SearchResult` (§22, крок 3)."""
    block_by_id = {block.block_id: block for block in document.blocks}
    section_by_id = {section.section_id: section for section in document.sections}
    freq = _word_frequencies(document)

    queries: list[SearchQuery] = []
    signal_hits: list[SignalHit] = []
    generated_a = 0
    retained_a = 0
    rejected: dict[str, int] = {}

    for donor in document.sentences:
        section = section_by_id.get(donor.section_id)
        block = block_by_id[donor.block_id]
        donor_normalized = normalize_text(donor.raw_text)
        sentence_base = donor.source.parts[0].raw_start

        # Сигнали каналу A обчислюються для КОЖНОГО речення незалежно від
        # типу розділу (§6.1: "сигнали і кандидати UNKNOWN лишаються в
        # діагностиці"; CLAUDE.md, правило №3 — нічого не подавляється
        # мовчки). Обмеження `CONTENT_SECTION_KINDS` застосовується нижче,
        # лише до перетворення кандидата на `SearchQuery`.
        signals = find_channel_a_signals(donor.normalized_text)

        for i, signal in enumerate(signals):
            raw_offsets = map_normalized_offsets(donor_normalized, signal.start, signal.end)
            sig_raw_start, sig_raw_end = raw_offsets[0][0], raw_offsets[-1][1]
            signal_hits.append(
                SignalHit(
                    evidence_id=f"sig-{donor.donor_id}-{i}",
                    channel=Channel.A,
                    rule_id=signal.rule_id,
                    source=SourceSpan(
                        parts=(
                            RawSpan(
                                block.block_id,
                                block.physical_page,
                                sentence_base + sig_raw_start,
                                sentence_base + sig_raw_end,
                            ),
                        )
                    ),
                    zone=TextZone.AUTHOR_TEXT,
                    score=score_channel_a(signals[i : i + 1]),
                    reason=signal.rule_id,
                )
            )

        if not signals:
            continue
        generated_a += 1

        if section is None:
            # Аномалія (донор без відповідного розділу); окрема причина, щоб
            # не змішувати з реальними типами розділів §6.1 нижче.
            rejected["section_unresolved"] = rejected.get("section_unresolved", 0) + 1
            continue
        if section.kind == SectionKind.UNKNOWN:
            # §6.1: нерозпізнаний авторський фрагмент — сигнал лишається
            # видимим (уже потрапив у `signal_hits`), але не дає запиту.
            rejected["section_unknown"] = rejected.get("section_unknown", 0) + 1
            continue
        if section.kind not in CONTENT_SECTION_KINDS:
            # §6.1: TITLE/TOC/ABSTRACT/BIBLIO/APPENDIX явно "не дають
            # запитів" — інша причина, ніж нерозпізнаний UNKNOWN.
            rejected["section_not_content_kind"] = rejected.get("section_not_content_kind", 0) + 1
            continue

        score = score_channel_a(signals)
        if score < MAIN_SELECTION_THRESHOLD:
            rejected["score_below_threshold_4"] = rejected.get("score_below_threshold_4", 0) + 1
            continue

        query = _build_channel_a_query(
            donor=donor,
            block=block,
            donor_normalized=donor_normalized,
            signals=signals,
            score=score,
            freq=freq,
        )
        if query is None:
            rejected["no_valid_windows"] = rejected.get("no_valid_windows", 0) + 1
            continue
        if query == "query_too_long":
            rejected["query_too_long"] = rejected.get("query_too_long", 0) + 1
            continue

        queries.append(query)
        retained_a += 1

    author_words = sum(
        section.author_words for section in document.sections if section.kind in CONTENT_SECTION_KINDS
    )

    warnings: list[str] = [
        "Запити N, B, K, T і L будуються на кроці 10; виробничі маркери вже доступні.",
    ]
    if not document.bibliography:
        warnings.append(
            "У документі не знайдено придатних бібліографічних записів або цитувань."
        )

    non_a_channels = tuple(c for c in Channel if c not in (Channel.A, Channel.D))
    generated_by_channel = ((Channel.A, generated_a),) + tuple((c, 0) for c in non_a_channels)
    retained_by_channel = ((Channel.A, retained_a),) + tuple((c, 0) for c in non_a_channels)
    attributed_by_channel = retained_by_channel

    return SearchResult(
        document=document,
        algo_version=ALGO_VERSION,
        dictionary_version=DICT_VERSION,
        queries=tuple(queries),
        shortfalls=(),
        signal_hits=tuple(signal_hits),
        calque_metrics=CalqueMetrics(
            author_words=author_words,
            tier1_hits=0,
            tier2_hits=0,
            tier3_hits=0,
            tier1_density=0.0,
            excluded_zone_hits=(),
        ),
        candidate_metrics=CandidateMetrics(
            generated_by_channel=generated_by_channel,
            retained_primary_by_channel=retained_by_channel,
            attributed_by_channel=attributed_by_channel,
            rejected_by_reason=tuple(sorted(rejected.items())),
        ),
        dedup_metrics=DedupMetrics(
            input_count=len(queries),
            component_count=len(queries),
            removed_count=0,
            merged_channel_attributions=0,
        ),
        warnings=tuple(warnings),
    )


def _word_frequencies(document: SearchDocument) -> dict[str, int]:
    freq: dict[str, int] = {}
    for donor in document.sentences:
        for token in tokenize(donor.raw_text, normalize_text(donor.raw_text)):
            if token.is_word and token.raw.isalpha():
                key = token.normalized.casefold()
                freq[key] = freq.get(key, 0) + 1
    return freq


def _is_proper_name_token(token: SearchToken, global_index: int) -> bool:
    """
    СПРОЩЕННЯ кроку 3: "будь-яке капіталізоване слово не на початку речення".

    Використовується і для бонусу вікна §13 ("+3 за власне ім'я"), і для
    надбавки ранжирування §11.3 — хоча §11.3 (рядки 989-993) дає вужче
    визначення: "ініціали + прізвище", "прізвище + ініціали" або два
    послідовних слова з великої літери поза початком речення. Точна
    реалізація §11.3 (окрема від бонусу вікна) — крок 10 разом з рештою
    евристик §15 переваги рідкісних слів/прізвищ/чисел.
    """
    return (
        token.is_word
        and token.raw.isalpha()
        and global_index > 0
        and token.raw[:1].isupper()
    )


def _is_number_token(token: SearchToken) -> bool:
    return token.is_word and token.raw[:1].isdigit()


def _is_long_content_word(token: SearchToken) -> bool:
    return (
        token.is_word
        and token.raw.isalpha()
        and len(token.raw) >= LONG_WORD_MIN_LEN
        and token.raw.casefold() not in _STOPWORDS
    )


def _is_rare_form_token(token: SearchToken, freq: dict[str, int]) -> bool:
    if not token.is_word or not token.raw.isalpha() or len(token.raw) < 4:
        return False
    key = token.normalized.casefold()
    if key in _STOPWORDS:
        return False
    count = freq.get(key, 0)
    return 1 <= count <= 2


def _score_window(
    word_tokens: list[SearchToken], start_idx: int, end_idx: int, raw_text: str, freq: dict[str, int]
) -> float:
    score = SIGNAL_COVERAGE_BONUS
    for global_i in range(start_idx, end_idx):
        token = word_tokens[global_i]
        if _is_proper_name_token(token, global_i):
            score += PROPER_NAME_BONUS
        if _is_number_token(token):
            score += NUMBER_DATE_BONUS
        if _is_rare_form_token(token, freq):
            score += RARE_FORM_BONUS
        if _is_long_content_word(token):
            score += LONG_WORD_BONUS
    window_text = raw_text[word_tokens[start_idx].raw_start : word_tokens[end_idx - 1].raw_end]
    score -= NORMATIVE_PENALTY * len(normative_marker_ids(window_text))
    return score


def _select_best_window(
    word_tokens: list[SearchToken],
    signal_raw_spans: list[tuple[int, int]],
    raw_text: str,
    freq: dict[str, int],
) -> tuple[int, int, float] | None:
    n = len(word_tokens)
    if n < WINDOW_MIN_WORDS:
        return None
    candidates: dict[tuple[int, int], float] = {}
    for sig_start, sig_end in signal_raw_spans:
        for start_idx in range(n):
            for size in range(WINDOW_MIN_WORDS, WINDOW_MAX_WORDS + 1):
                end_idx = start_idx + size
                if end_idx > n:
                    break
                first_tok = word_tokens[start_idx]
                last_tok = word_tokens[end_idx - 1]
                if first_tok.raw_start <= sig_start and sig_end <= last_tok.raw_end:
                    key = (start_idx, end_idx)
                    if key not in candidates:
                        candidates[key] = _score_window(word_tokens, start_idx, end_idx, raw_text, freq)
    if not candidates:
        return None
    # §13 крок 5: за рівності — коротше, потім раніше.
    best_key = min(candidates, key=lambda k: (-candidates[k], k[1] - k[0], k[0]))
    return best_key[0], best_key[1], candidates[best_key]


def _trim_window_to_limit(
    word_tokens: list[SearchToken],
    start_idx: int,
    end_idx: int,
    signal_raw_spans: list[tuple[int, int]],
    raw_text: str,
) -> tuple[int, int] | None:
    lo, hi = start_idx, end_idx

    def quoted_len(lo: int, hi: int) -> int:
        return len(raw_text[word_tokens[lo].raw_start : word_tokens[hi - 1].raw_end]) + 2

    while quoted_len(lo, hi) > MAX_QUERY_CHARS:
        if hi - lo <= 1:
            return None
        left_tok, right_tok = word_tokens[lo], word_tokens[hi - 1]
        left_has_signal = any(left_tok.raw_start < e and s < left_tok.raw_end for s, e in signal_raw_spans)
        right_has_signal = any(right_tok.raw_start < e and s < right_tok.raw_end for s, e in signal_raw_spans)
        if left_has_signal and right_has_signal:
            return None
        if left_has_signal:
            hi -= 1
        elif right_has_signal:
            lo += 1
        elif len(left_tok.raw) <= len(right_tok.raw):
            lo += 1
        else:
            hi -= 1
    return lo, hi


def _guess_query_language(text: str) -> Language:
    """
    Плейсхолдер до повного `search/language.py` (крок 7): лише щоб поле
    `query_language` мало недовільне значення. Не є джерелом істини про мову.
    """
    has_uk = any(ch in _UK_ONLY_CHARS for ch in text)
    has_ru = any(ch in _RU_ONLY_CHARS for ch in text)
    if has_uk and has_ru:
        return Language.MIXED
    if has_uk:
        return Language.UK
    if has_ru:
        return Language.RU
    return Language.UNKNOWN


def _build_pdf_anchor(
    word_tokens: list[SearchToken], raw_text: str, block_id: str, physical_page: int, sentence_base: int
) -> tuple[str, SourceSpan, bool]:
    """
    Спрощений якір (§15): перші 8–15 слів донора. Повна евристика переваги
    рідкісних слів/прізвищ/чисел — крок 10. Повертає (текст, джерело,
    is_fallback) — `is_fallback=True`, якщо слів донора менше мінімуму і
    показано весь донор (§15 «якщо стійкого вікна немає»).
    """
    n = len(word_tokens)
    if n == 0:
        return raw_text, SourceSpan(parts=(RawSpan(block_id, physical_page, sentence_base, sentence_base + len(raw_text)),)), True
    if n < ANCHOR_MIN_WORDS:
        text = raw_text
        source = SourceSpan(
            parts=(RawSpan(block_id, physical_page, sentence_base, sentence_base + len(raw_text)),)
        )
        return text, source, True
    size = min(n, ANCHOR_MAX_WORDS)
    first_tok, last_tok = word_tokens[0], word_tokens[size - 1]
    text = raw_text[first_tok.raw_start : last_tok.raw_end]
    source = SourceSpan(
        parts=(
            RawSpan(
                block_id,
                physical_page,
                sentence_base + first_tok.raw_start,
                sentence_base + last_tok.raw_end,
            ),
        )
    )
    return text, source, False


def _build_channel_a_query(
    *,
    donor: SentenceDonor,
    block: SearchBlock,
    donor_normalized,
    signals,
    score: float,
    freq: dict[str, int],
) -> SearchQuery | None | str:
    """Повертає `SearchQuery`, `None` (немає вікна) або `"query_too_long"`."""
    word_tokens = [t for t in tokenize(donor.raw_text, donor_normalized) if t.is_word]

    signal_raw_spans: list[tuple[int, int]] = []
    for signal in signals:
        offsets = map_normalized_offsets(donor_normalized, signal.start, signal.end)
        signal_raw_spans.append((offsets[0][0], offsets[-1][1]))

    best = _select_best_window(word_tokens, signal_raw_spans, donor.raw_text, freq)
    if best is None:
        return None
    start_idx, end_idx, window_score = best

    trimmed = _trim_window_to_limit(word_tokens, start_idx, end_idx, signal_raw_spans, donor.raw_text)
    if trimmed is None:
        return "query_too_long"
    start_idx, end_idx = trimmed

    sentence_base = donor.source.parts[0].raw_start
    phrase_raw_start = word_tokens[start_idx].raw_start
    phrase_raw_end = word_tokens[end_idx - 1].raw_end
    phrase_text = donor.raw_text[phrase_raw_start:phrase_raw_end]
    phrase_source = SourceSpan(
        parts=(
            RawSpan(
                block.block_id,
                block.physical_page,
                sentence_base + phrase_raw_start,
                sentence_base + phrase_raw_end,
            ),
        )
    )

    parts = (
        QueryPart(text="«", origin=QueryPartOrigin.SYSTEM_LITERAL, origin_id="quote_open", source=None),
        QueryPart(text=phrase_text, origin=QueryPartOrigin.SOURCE_PHRASE, origin_id=None, source=phrase_source),
        QueryPart(text="»", origin=QueryPartOrigin.SYSTEM_LITERAL, origin_id="quote_close", source=None),
    )
    query_text = "".join(part.text for part in parts)
    if query_text != f"«{phrase_text}»" or len(query_text) > MAX_QUERY_CHARS:
        return "query_too_long"

    anchor_text, anchor_source, _anchor_fallback = _build_pdf_anchor(
        word_tokens, donor.raw_text, block.block_id, block.physical_page, sentence_base
    )

    donor_id = donor.donor_id
    reasons = tuple(sorted({s.rule_id for s in signals}))
    evidence_ids = tuple(f"sig-{donor_id}-{i}" for i in range(len(signals)))

    rank_bonus = 0.0
    if any(_is_proper_name_token(t, i) for i, t in enumerate(word_tokens)):
        rank_bonus += 1.0
    if any(_is_number_token(t) for t in word_tokens):
        rank_bonus += 1.0

    query_id = _build_query_id(donor_id, Channel.A, None, query_text, parts)

    return SearchQuery(
        donor_id=donor_id,
        query_id=query_id,
        block_id=block.block_id,
        section_id=donor.section_id,
        sentence_ordinal=donor.sentence_ordinal,
        primary_channel=Channel.A,
        attributed_channels=(Channel.A,),
        subtype=None,
        query_language=_guess_query_language(query_text),
        selection_stage=1,
        query_text=query_text,
        parts=parts,
        donor_text=donor.raw_text,
        donor_source=donor.source,
        pdf_anchor=anchor_text,
        pdf_anchor_source=anchor_source,
        physical_page=block.physical_page,
        score=score,
        rank_score=score + rank_bonus,
        evidence_ids=evidence_ids,
        reasons=reasons,
    )


def _build_query_id(
    donor_id: str, channel: Channel, subtype: str | None, query_text: str, parts: tuple[QueryPart, ...]
) -> str:
    normalized_query = normalize_text(query_text).text.casefold()
    origin_repr = "|".join(f"{p.origin.value}:{p.origin_id or ''}" for p in parts)
    payload = f"{donor_id}|{channel.value}|{subtype or ''}|{normalized_query}|{origin_repr}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
