"""
search/query_builder.py
Побудова доказово відтворюваних запитів A/N/B/K/T/L, вікна, квоти,
драбина пріоритету, дедуплікація та чесний недобір. Специфікація —
PLAN_SEARCH.md, §§10–15.

Кожний змістовний `QueryPart` має простежуване походження, а готовий текст
повторно складається з частин. Кандидати відбираються незалежно по секціях:
числові пороги A/N/B/K, потім T і активовані L; компоненти дублів зберігають
об'єднані атрибуції, а нестача до десяти описується `SectionShortfall`.

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
import math
import re
from collections import Counter
from dataclasses import dataclass, replace

from search import ALGO_VERSION
from search.bibliography import (
    _build_citation_index,
    _linked_ru_entries_by_donor,
    donor_ids_for_mention,
)
from search.calques import (
    DICT_VERSION,
    CalqueHit,
    _compute_metrics_from_analysis,
    find_calques,
    find_calques_with_rejections,
    rule_by_id,
)
from search.language import detect_language, reliable_ru_content_words
from search.markers import (
    STOPWORDS,
    CandidateSignal,
    channel_a_signals,
    evaluate_candidate,
    find_channel_b_signals,
    find_channel_n_signals,
    find_channel_t_signals,
    normative_marker_ids,
    rare_word_forms,
    score_channel_a,
    find_channel_a_signals,
)
from search.normalization import map_normalized_offsets, normalize_text, tokenize
from search.types import (
    CONTENT_SECTION_KINDS,
    CalqueMetrics,
    Confidence,
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
    NormalizedText,
    SectionKind,
    SectionShortfall,
    ShortfallReason,
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
TARGET_QUERIES_PER_SECTION = 10
MAX_QUERIES_PER_SECTION = 12
TOP_VISIBLE_PER_SECTION = 5
DEDUP_JACCARD_THRESHOLD = 0.65
DEDUP_COMMON_RUN_WORDS = 5
MAX_PER_BLOCK = 2
MAX_PER_PAGE = 3
RELAXED_MAX_PER_BLOCK = 3
RELAXED_MAX_PER_PAGE = 4
NORMATIVE_HEAVY_RATIO = 0.60

_STOPWORDS = STOPWORDS

_SYSTEM_LITERAL_TEXT_BY_ORIGIN_ID = {
    "quote_open": "«",
    "open": "«",
    "quote_close": "»",
    "close": "»",
    "quote_close_space": "» ",
    "space": " ",
    "definition_literal": "определение",
}
_SYSTEM_SPACE_ORIGIN_RE = re.compile(r"space_\d+\Z")

_UK_ONLY_CHARS = set("іїєґІЇЄҐ")
_RU_ONLY_CHARS = set("ыэъёЫЭЪЁ")

_INITIALS_SURNAME_RE = re.compile(
    r"(?:(?P<initials>(?:[А-ЯІЇЄҐ]\.(?:\s*|$)){1,2})\s*(?P<surname>[А-ЯІЇЄҐ][а-яіїєґ'’ʼ-]{2,})"
    r"|(?P<surname_first>[А-ЯІЇЄҐ][а-яіїєґ'’ʼ-]{2,})\s+(?P<initials_last>(?:[А-ЯІЇЄҐ]\.(?:\s*|$)){1,2}))",
    re.UNICODE,
)
_DEFINITION_RE = re.compile(
    r"\b(?:визначає|визначено)(?:\s+[^\W\d_]+){0,2}\s+як\b",
    re.IGNORECASE | re.UNICODE,
)
_OPINION_SURNAME_RE = re.compile(
    r"\bна\s+думку\s+[А-ЯІЇЄҐ][а-яіїєґ'’ʼ-]{2,}", re.UNICODE
)
_JOINED_HYPHEN_RE = re.compile(r"-\s*\r?\n\s*")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")  # ru-data


@dataclass(frozen=True)
class SurnameEvidence:
    """Прізвище лише з конструкції поряд з ініціалами (§12.7)."""

    surname: str
    raw_start: int
    raw_end: int


@dataclass
class _DonorQueryContext:
    """Похідні ознаки донора, спільні для всіх його каналів і вікон."""

    normalized_text: NormalizedText
    word_tokens: list[SearchToken]
    proper_name_indexes: frozenset[int]
    has_two_capitalized_words: bool
    meaningful_number_flags: tuple[bool, ...]
    rare_form_flags: tuple[bool, ...]
    long_content_word_flags: tuple[bool, ...]
    number_bonuses: tuple[float, ...]
    token_bonus_prefix: tuple[float, ...]
    surname_evidence: tuple[SurnameEvidence, ...]
    number_parts: tuple[QueryPart, ...]
    normative_penalty_cache: dict[tuple[int, int], float]


def compose_query_parts(parts: tuple[QueryPart, ...]) -> str:
    """Єдиний спосіб скласти канонічний рядок із provenance-частин (§13)."""

    return "".join(part.text for part in parts)


def validate_query_parts(parts: tuple[QueryPart, ...], query_text: str) -> bool:
    """Відхилити рядок, який не відтворюється або має зміст без походження."""

    if not parts or compose_query_parts(parts) != query_text or len(query_text) > MAX_QUERY_CHARS:
        return False
    for part in parts:
        if not part.text:
            return False
        if part.origin == QueryPartOrigin.SYSTEM_LITERAL:
            if part.origin_id is None or part.source is not None:
                return False
            expected_text = _SYSTEM_LITERAL_TEXT_BY_ORIGIN_ID.get(part.origin_id)
            if expected_text is None and (
                not isinstance(part.origin_id, str)
                or _SYSTEM_SPACE_ORIGIN_RE.fullmatch(part.origin_id) is None
            ):
                return False
            if part.text != (expected_text or " "):
                return False
        elif part.origin in (QueryPartOrigin.CALQUE_RULE, QueryPartOrigin.RU_REFERENCE):
            if part.origin_id is None:
                return False
        elif part.source is None:
            return False
    return True


def extract_surname_evidence(raw_text: str) -> tuple[SurnameEvidence, ...]:
    """Унікальні прізвища біля ініціалів у порядку появи."""

    found: list[SurnameEvidence] = []
    seen: set[tuple[str, int, int]] = set()
    for match in _INITIALS_SURNAME_RE.finditer(raw_text):
        group = "surname" if match.group("surname") else "surname_first"
        surname = match.group(group)
        item = (surname, match.start(group), match.end(group))
        if item not in seen:
            seen.add(item)
            found.append(SurnameEvidence(*item))
    return tuple(found)


def transliterate_surname(surname: str) -> str:
    """Детермінована українсько-російська транслітерація прізвища (§12.7)."""

    original = surname.casefold().replace("’", "'").replace("ʼ", "'")
    suffix = ""
    if original.endswith("ський"):
        original, suffix = original[:-5], "ский"
    elif original.endswith("цький"):
        original, suffix = original[:-5], "цкий"
    text = original
    text = re.sub(r"'я", "ья", text)
    text = re.sub(r"'ю", "ью", text)
    text = re.sub(r"'є", "ье", text)
    text = text.replace("і", "и").replace("ї", "и").replace("є", "е").replace("ґ", "г")
    chars: list[str] = []
    for index, char in enumerate(text):
        if char == "и" and (index == 0 or text[index - 1] not in "жчшщц"):
            chars.append("ы")
        else:
            chars.append(char)
    result = "".join(chars) + suffix
    return result[:1].upper() + result[1:] if surname[:1].isupper() else result


def build_source_channel_query(
    *,
    donor: SentenceDonor,
    block: SearchBlock,
    channel: Channel,
    signals: tuple[CandidateSignal, ...],
    score: float,
    freq: dict[str, int],
    subtype: str | None = None,
    query_context: _DonorQueryContext | None = None,
) -> SearchQuery | None | str:
    """Канонічний A/N/B/T/L або K1-запит із точного вихідного вікна."""

    context = query_context or _build_donor_query_context(donor, block, freq)
    word_tokens = context.word_tokens
    signal_spans = [(signal.raw_start, signal.raw_end) for signal in signals]
    contextual_n = channel == Channel.N and any(
        signal.rule_id == "N.novelty_heading" for signal in signals
    )
    if channel == Channel.L or contextual_n:
        best = _select_best_unanchored_window(
            word_tokens, donor.raw_text, freq, context=context
        )
    else:
        best = _select_best_window(
            word_tokens, signal_spans, donor.raw_text, freq, context=context
        )
    if best is None:
        return None
    start_idx, end_idx, _ = best
    protected = (() if contextual_n else signal_spans) or [
        (word_tokens[start_idx].raw_start, word_tokens[end_idx - 1].raw_end)
    ]
    trimmed = _trim_window_to_limit(word_tokens, start_idx, end_idx, protected, donor.raw_text)
    if trimmed is None:
        return "query_too_long"
    start_idx, end_idx = trimmed
    raw_start = word_tokens[start_idx].raw_start
    raw_end = word_tokens[end_idx - 1].raw_end
    phrase = donor.raw_text[raw_start:raw_end]
    source = _donor_source(donor, block, raw_start, raw_end)
    quoted = channel in (Channel.A, Channel.N, Channel.T, Channel.K)
    parts = _phrase_parts(phrase, source, quoted=quoted)
    query_text = compose_query_parts(parts)
    if not validate_query_parts(parts, query_text):
        return "query_too_long"
    return _make_query(
        donor=donor,
        block=block,
        channel=channel,
        subtype=subtype,
        score=score,
        parts=parts,
        reasons=tuple(sorted({signal.rule_id for signal in signals})),
        evidence_ids=tuple(f"sig-{donor.donor_id}-{channel.value}-{i}" for i in range(len(signals))),
        freq=freq,
        query_context=context,
    )


def build_k_queries(
    document: SearchDocument,
    donor: SentenceDonor,
    block: SearchBlock,
    *,
    score: float,
    freq: dict[str, int],
    calque_hits: tuple[CalqueHit, ...] | None = None,
    linked_ru_entries: tuple[tuple[object, Confidence, int], ...] | None = None,
    query_context: _DonorQueryContext | None = None,
) -> tuple[SearchQuery, ...]:
    """Не більше одного K1, двох K2 та одного K3 для одного речення (§12.6)."""

    context = query_context or _build_donor_query_context(donor, block, freq)
    donor_start = donor.source.parts[0].raw_start
    donor_end = donor.source.parts[-1].raw_end
    hits = tuple(
        hit for hit in (calque_hits if calque_hits is not None else find_calques(block))
        if hit.zone == TextZone.AUTHOR_TEXT and hit.raw_start < donor_end and hit.raw_end > donor_start
    )
    hits = tuple(sorted(hits, key=lambda hit: (hit.tier, hit.raw_start, hit.rule_id)))
    if not hits:
        return ()

    relative_signals = tuple(
        CandidateSignal(
            Channel.K,
            f"K.{hit.rule_id}",
            max(hit.raw_start - donor_start, 0),
            min(hit.raw_end - donor_start, len(donor.raw_text)),
            0.0,
            f"K.tier{hit.tier}",
        )
        for hit in hits
    )
    result: list[SearchQuery] = []
    k1 = build_source_channel_query(
        donor=donor,
        block=block,
        channel=Channel.K,
        signals=(relative_signals[0],),
        score=score,
        freq=freq,
        subtype="K1",
        query_context=context,
    )
    if isinstance(k1, SearchQuery):
        result.append(k1)

    linked = (
        _linked_ru_entries(document, donor, block)
        if linked_ru_entries is None
        else linked_ru_entries
    )
    surnames = context.surname_evidence
    numbers = context.number_parts
    k2_contexts: list[tuple[object | None, SurnameEvidence | None]] = []
    if linked:
        k2_contexts.extend((item[0], surnames[0] if surnames else None) for item in linked[:2])
    elif surnames:
        k2_contexts.append((None, surnames[0]))
    for index, (entry, surname) in enumerate(k2_contexts[:2]):
        hit = hits[min(index, len(hits) - 1)]
        query = _build_k2_query(
            donor,
            block,
            hit,
            entry,
            surname,
            numbers,
            score,
            freq,
            query_context=context,
        )
        if query is not None:
            result.append(query)

    k3 = _build_k3_query(
        document,
        donor,
        block,
        hits[0],
        linked,
        surnames,
        score,
        freq,
        query_context=context,
    )
    if k3 is not None:
        result.append(k3)
    return tuple(result)


def _build_k2_query(
    donor,
    block,
    hit,
    entry,
    surname,
    numbers,
    score,
    freq,
    *,
    query_context: _DonorQueryContext | None = None,
) -> SearchQuery | None:
    rule = rule_by_id(hit.rule_id)
    if not rule.ru_origin.strip() or (entry is None and surname is None):
        return None
    hit_source = SourceSpan((RawSpan(block.block_id, block.physical_page, hit.raw_start, hit.raw_end),))
    content: list[QueryPart] = [
        QueryPart(rule.ru_origin, QueryPartOrigin.CALQUE_RULE, rule.rule_id, hit_source)
    ]
    if entry is not None and entry.title and entry.title_confidence in (Confidence.HIGH, Confidence.MEDIUM):
        words = reliable_ru_content_words(entry.title)
        if words:
            text = " ".join(item.word for item in words[:4])
            content.append(QueryPart(text, QueryPartOrigin.RU_REFERENCE, entry.entry_id, entry.title_source or entry.source))
    if surname is not None:
        content.append(QueryPart(
            transliterate_surname(surname.surname),
            QueryPartOrigin.SURNAME_TRANSLITERATION,
            surname.surname,
            _donor_source(donor, block, surname.raw_start, surname.raw_end),
        ))
    content.extend(numbers[:2])
    parts = _space_join_parts(tuple(content))
    query_text = compose_query_parts(parts)
    if not validate_query_parts(parts, query_text):
        return None
    return _make_query(
        donor=donor,
        block=block,
        channel=Channel.K,
        subtype="K2",
        score=score,
        parts=parts,
        reasons=(f"K.{hit.rule_id}",),
        evidence_ids=(f"k2-{donor.donor_id}-{hit.rule_id}-{getattr(entry, 'entry_id', 'surname')}",),
        freq=freq,
        linked_ru=entry is not None,
        query_context=query_context,
    )


def _build_k3_query(
    document,
    donor,
    block,
    hit,
    linked,
    surnames,
    score,
    freq,
    *,
    query_context: _DonorQueryContext | None = None,
) -> SearchQuery | None:
    context = query_context or _build_donor_query_context(donor, block, freq)
    if not surnames or not _has_definition_marker(
        donor.raw_text, normalized_text=context.normalized_text.text
    ):
        return None
    rule = rule_by_id(hit.rule_id)
    if not rule.ru_origin.strip():
        return None
    surname = surnames[0]
    entry = next(
        (
            item[0] for item in linked
            if item[0].title and item[0].title_confidence in (Confidence.HIGH, Confidence.MEDIUM)
        ),
        None,
    )
    surname_part = QueryPart(
        transliterate_surname(surname.surname),
        QueryPartOrigin.SURNAME_TRANSLITERATION,
        surname.surname,
        _donor_source(donor, block, surname.raw_start, surname.raw_end),
    )
    if entry is not None:
        subject = QueryPart(entry.title, QueryPartOrigin.RU_REFERENCE, entry.entry_id, entry.title_source or entry.source)
    else:
        hit_source = SourceSpan((RawSpan(block.block_id, block.physical_page, hit.raw_start, hit.raw_end),))
        subject = QueryPart(rule.ru_origin, QueryPartOrigin.CALQUE_RULE, rule.rule_id, hit_source)
    parts = (
        surname_part,
        QueryPart(" ", QueryPartOrigin.SYSTEM_LITERAL, "space", None),
        QueryPart("«", QueryPartOrigin.SYSTEM_LITERAL, "quote_open", None),
        subject,
        QueryPart("» ", QueryPartOrigin.SYSTEM_LITERAL, "quote_close_space", None),
        QueryPart("определение", QueryPartOrigin.SYSTEM_LITERAL, "definition_literal", None),
    )
    query_text = compose_query_parts(parts)
    if not validate_query_parts(parts, query_text):
        return None
    return _make_query(
        donor=donor,
        block=block,
        channel=Channel.K,
        subtype="K3",
        score=score,
        parts=parts,
        reasons=(f"K.{hit.rule_id}", "definition_marker"),
        evidence_ids=(f"k3-{donor.donor_id}-{hit.rule_id}",),
        freq=freq,
        linked_ru=entry is not None,
        query_context=context,
    )


def _phrase_parts(text: str, source: SourceSpan, *, quoted: bool) -> tuple[QueryPart, ...]:
    phrase = QueryPart(text, QueryPartOrigin.SOURCE_PHRASE, None, source)
    if not quoted:
        return (phrase,)
    return (
        QueryPart("«", QueryPartOrigin.SYSTEM_LITERAL, "quote_open", None),
        phrase,
        QueryPart("»", QueryPartOrigin.SYSTEM_LITERAL, "quote_close", None),
    )


def _space_join_parts(content: tuple[QueryPart, ...]) -> tuple[QueryPart, ...]:
    result: list[QueryPart] = []
    for index, part in enumerate(content):
        if index:
            result.append(QueryPart(" ", QueryPartOrigin.SYSTEM_LITERAL, f"space_{index}", None))
        result.append(part)
    return tuple(result)


def _donor_source(
    donor: SentenceDonor, block: SearchBlock, relative_start: int, relative_end: int
) -> SourceSpan:
    base = donor.source.parts[0].raw_start
    return SourceSpan((
        RawSpan(block.block_id, block.physical_page, base + relative_start, base + relative_end),
    ))


def _build_query_context(
    raw_text: str,
    normalized_text: NormalizedText,
    word_tokens: list[SearchToken],
    freq: dict[str, int],
) -> _DonorQueryContext:
    """Готує ознаки токенів, які не залежать від конкретного вікна."""

    surname_evidence = extract_surname_evidence(raw_text)
    proper_name_indexes = _proper_name_indexes(
        word_tokens,
        raw_text,
        surname_evidence=surname_evidence,
    )
    meaningful_number_flags = tuple(
        _is_number_token(token) and _is_meaningful_number(token, raw_text)
        for token in word_tokens
    )
    rare_form_flags = tuple(_is_rare_form_token(token, freq) for token in word_tokens)
    long_content_word_flags = tuple(_is_long_content_word(token) for token in word_tokens)
    number_bonuses = tuple(
        NUMBER_DATE_BONUS if meaningful else 0.0
        for meaningful in meaningful_number_flags
    )
    token_bonus_prefix = [0.0]
    for index, (token, meaningful, rare, long_word, number_bonus) in enumerate(
        zip(
            word_tokens,
            meaningful_number_flags,
            rare_form_flags,
            long_content_word_flags,
            number_bonuses,
        )
    ):
        bonus = (
            (PROPER_NAME_BONUS if index in proper_name_indexes else 0.0)
            + number_bonus
            + (RARE_FORM_BONUS if rare else 0.0)
            + (LONG_WORD_BONUS if long_word else 0.0)
        )
        token_bonus_prefix.append(token_bonus_prefix[-1] + bonus)
    return _DonorQueryContext(
        normalized_text=normalized_text,
        word_tokens=word_tokens,
        proper_name_indexes=proper_name_indexes,
        has_two_capitalized_words=_has_two_capitalized_words(word_tokens),
        meaningful_number_flags=meaningful_number_flags,
        rare_form_flags=rare_form_flags,
        long_content_word_flags=long_content_word_flags,
        number_bonuses=number_bonuses,
        token_bonus_prefix=tuple(token_bonus_prefix),
        surname_evidence=surname_evidence,
        number_parts=(),
        normative_penalty_cache={},
    )


def _build_donor_query_context(
    donor: SentenceDonor, block: SearchBlock, freq: dict[str, int]
) -> _DonorQueryContext:
    """Будує спільний контекст оцінки для одного донора."""

    normalized_text = normalize_text(donor.raw_text)
    word_tokens = [token for token in tokenize(donor.raw_text, normalized_text) if token.is_word]
    context = _build_query_context(donor.raw_text, normalized_text, word_tokens, freq)
    context.number_parts = _number_parts(donor, block, query_context=context)
    return context


def _number_parts(
    donor: SentenceDonor,
    block: SearchBlock,
    *,
    query_context: _DonorQueryContext | None = None,
) -> tuple[QueryPart, ...]:
    if query_context is not None:
        return tuple(
            QueryPart(
                token.raw,
                QueryPartOrigin.LITERAL_NUMBER,
                None,
                _donor_source(donor, block, token.raw_start, token.raw_end),
            )
            for token, meaningful in zip(
                query_context.word_tokens,
                query_context.meaningful_number_flags,
            )
            if meaningful
        )
    result: list[QueryPart] = []
    normalized = normalize_text(donor.raw_text)
    for token in tokenize(donor.raw_text, normalized):
        if _is_number_token(token) and _is_meaningful_number(token, donor.raw_text):
            result.append(QueryPart(
                token.raw,
                QueryPartOrigin.LITERAL_NUMBER,
                None,
                _donor_source(donor, block, token.raw_start, token.raw_end),
            ))
    return tuple(result)


def _linked_ru_entries(document: SearchDocument, donor: SentenceDonor, block: SearchBlock):
    by_id = {entry.entry_id: entry for entry in document.bibliography if entry.language == Language.RU}
    linked: list[tuple[object, Confidence, int]] = []
    seen: set[str] = set()
    donor_mid = (donor.source.parts[0].raw_start + donor.source.parts[-1].raw_end) // 2
    for mention in document.citations:
        if donor.donor_id not in donor_ids_for_mention(document, mention):
            continue
        mention_start = mention.source.parts[0].raw_start if mention.source.parts else donor_mid
        for entry_id in mention.entry_ids:
            entry = by_id.get(entry_id)
            if entry is not None and entry_id not in seen:
                seen.add(entry_id)
                linked.append((entry, mention.confidence, abs(mention_start - donor_mid)))
    confidence_order = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
    linked.sort(key=lambda item: (
        confidence_order[item[1]],
        0 if item[0].title_confidence == Confidence.HIGH else 1 if item[0].title_confidence == Confidence.MEDIUM else 2,
        item[2],
        item[0].entry_id,
    ))
    return tuple(linked)


def _has_definition_marker(raw_text: str, *, normalized_text: str | None = None) -> bool:
    normalized = normalized_text if normalized_text is not None else normalize_text(raw_text).text
    has_understand = any(
        signal.rule_id == "A.under_understand" for signal in find_channel_a_signals(normalized)
    )
    return has_understand or bool(_DEFINITION_RE.search(normalized)) or bool(_OPINION_SURNAME_RE.search(raw_text))


def _make_query(
    *,
    donor: SentenceDonor,
    block: SearchBlock,
    channel: Channel,
    subtype: str | None,
    score: float,
    parts: tuple[QueryPart, ...],
    reasons: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    freq: dict[str, int],
    linked_ru: bool = False,
    query_context: _DonorQueryContext | None = None,
) -> SearchQuery:
    query_text = compose_query_parts(parts)
    context = query_context or _build_donor_query_context(donor, block, freq)
    word_tokens = context.word_tokens
    sentence_base = donor.source.parts[0].raw_start
    anchor_text, anchor_source, anchor_fallback = _build_pdf_anchor(
        word_tokens,
        donor.raw_text,
        block.block_id,
        block.physical_page,
        sentence_base,
        freq=freq,
        query_context=context,
    )
    rank_bonus = 0.0
    if context.surname_evidence or context.has_two_capitalized_words:
        rank_bonus += 1.0
    if any(context.meaningful_number_flags):
        rank_bonus += 1.0
    if linked_ru:
        rank_bonus += 2.0
    query_id = _build_query_id(donor.donor_id, channel, subtype, query_text, parts)
    reason_set = set(reasons)
    if anchor_fallback:
        reason_set.add("pdf_anchor_fallback")
    return SearchQuery(
        donor_id=donor.donor_id,
        query_id=query_id,
        block_id=block.block_id,
        section_id=donor.section_id,
        sentence_ordinal=donor.sentence_ordinal,
        primary_channel=channel,
        attributed_channels=(channel,),
        subtype=subtype,
        query_language=detect_language(query_text).language,
        selection_stage=_selection_stage(score, channel),
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
        reasons=tuple(sorted(reason_set)),
    )


def _selection_stage(score: float, channel: Channel) -> int:
    if channel == Channel.T:
        return 4
    if channel == Channel.L:
        return 5
    if score >= 4:
        return 1
    if score >= 3:
        return 2
    return 3


def _select_best_unanchored_window(
    word_tokens: list[SearchToken],
    raw_text: str,
    freq: dict[str, int],
    *,
    context: _DonorQueryContext | None = None,
) -> tuple[int, int, float] | None:
    context = context or _build_query_context(
        raw_text,
        normalize_text(raw_text),
        word_tokens,
        freq,
    )
    if len(word_tokens) < WINDOW_MIN_WORDS:
        return None
    candidates: list[tuple[float, int, int, int]] = []
    for start in range(len(word_tokens)):
        for size in range(WINDOW_MIN_WORDS, WINDOW_MAX_WORDS + 1):
            end = start + size
            if end > len(word_tokens):
                break
            score = _score_window(
                word_tokens,
                start,
                end,
                raw_text,
                freq,
                context=context,
            ) - SIGNAL_COVERAGE_BONUS
            candidates.append((-score, size, start, end))
    _, _, start, end = min(candidates)
    return start, end, -min(candidates)[0]


def _has_two_capitalized_words(tokens: list[SearchToken]) -> bool:
    for index in range(1, len(tokens) - 1):
        if tokens[index].raw[:1].isupper() and tokens[index + 1].raw[:1].isupper():
            return True
    return False


def _is_meaningful_number(token: SearchToken, raw_text: str) -> bool:
    prefix = raw_text[max(0, token.raw_start - 20):token.raw_start].casefold()
    return not re.search(r"(?:статт\w*|частин\w*|пункт\w*)\s*$", prefix)


def _build_step3_result(document: SearchDocument) -> SearchResult:
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
        "Добір T/L, квоти й дедуплікація застосовуються на кроці 11.",
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


def build_search_result(document: SearchDocument) -> SearchResult:
    """Побудувати, дедуплікувати й відібрати канонічні A/N/B/K/T/L-запити."""

    result, _ = build_search_result_with_candidates(document)
    return result


def build_search_result_with_candidates(
    document: SearchDocument,
) -> tuple[SearchResult, tuple[SearchQuery, ...]]:
    """Повернути результат і той самий pre-selection pool для ручної якості."""

    blocks = {block.block_id: block for block in document.blocks}
    sections = {section.section_id: section for section in document.sections}
    calque_analysis = {
        block.block_id: find_calques_with_rejections(block)
        for block in document.blocks
    }
    citation_index = _build_citation_index(document)
    linked_ru_entries_by_donor = _linked_ru_entries_by_donor(document, citation_index)
    rare_forms = rare_word_forms(document)
    freq = _word_frequencies(document)
    queries: list[SearchQuery] = []
    signal_hits: list[SignalHit] = []
    generated = {channel: 0 for channel in Channel if channel != Channel.D}
    retained = {channel: 0 for channel in Channel if channel != Channel.D}
    rejected: dict[str, int] = {}

    for donor in document.sentences:
        block = blocks[donor.block_id]
        section = sections.get(donor.section_id)
        query_context = _build_donor_query_context(donor, block, freq)
        block_calques = calque_analysis[block.block_id]
        evaluation = evaluate_candidate(
            donor,
            block,
            section,
            rare_forms,
            calque_analysis=block_calques,
        )
        grouped: dict[Channel, list[CandidateSignal]] = {
            channel: [] for channel in Channel if channel != Channel.D
        }
        for signal in evaluation.signals:
            grouped[signal.channel].append(signal)
        if evaluation.channel_l_candidate:
            generated[Channel.L] += 1
        for channel, items in grouped.items():
            if items:
                generated[channel] += 1
            for index, signal in enumerate(items):
                signal_hits.append(SignalHit(
                    evidence_id=f"sig-{donor.donor_id}-{channel.value}-{index}",
                    channel=channel,
                    rule_id=signal.rule_id,
                    source=_donor_source(donor, block, signal.raw_start, signal.raw_end),
                    zone=TextZone.AUTHOR_TEXT,
                    score=signal.score,
                    reason=signal.reason,
                ))

        if section is None:
            _increment(rejected, "section_unresolved")
            continue
        if section.kind == SectionKind.UNKNOWN:
            _increment(rejected, "section_unknown")
            continue
        if section.kind not in CONTENT_SECTION_KINDS:
            _increment(rejected, "section_not_content_kind")
            continue

        primary_channels = tuple(
            channel for channel in (Channel.A, Channel.N, Channel.B, Channel.K)
            if grouped[channel]
        )
        if evaluation.final_score < 2.0:
            for channel in primary_channels:
                _increment(rejected, f"score_below_threshold_2:{channel.value}")
        else:
            for channel in (Channel.A, Channel.N, Channel.B):
                items = tuple(grouped[channel])
                if not items:
                    continue
                query = build_source_channel_query(
                    donor=donor,
                    block=block,
                    channel=channel,
                    signals=items,
                    score=evaluation.final_score,
                    freq=freq,
                    query_context=query_context,
                )
                if isinstance(query, SearchQuery):
                    queries.append(query)
                else:
                    _increment(rejected, str(query or "no_valid_windows"))

        if grouped[Channel.K] and evaluation.final_score >= 2.0:
            evidence_ids = tuple(
                f"sig-{donor.donor_id}-K-{index}" for index in range(len(grouped[Channel.K]))
            )
            k_queries = build_k_queries(
                document,
                donor,
                block,
                score=evaluation.final_score,
                freq=freq,
                calque_hits=block_calques[0],
                linked_ru_entries=linked_ru_entries_by_donor.get(donor.donor_id, ()),
                query_context=query_context,
            )
            for query in k_queries:
                queries.append(replace(query, evidence_ids=evidence_ids))
            if not k_queries:
                _increment(rejected, "k_no_buildable_subtype")

        if grouped[Channel.T]:
            query = build_source_channel_query(
                donor=donor,
                block=block,
                channel=Channel.T,
                signals=tuple(grouped[Channel.T]),
                score=0.0,
                freq=freq,
                query_context=query_context,
            )
            if isinstance(query, SearchQuery):
                queries.append(query)
        if evaluation.channel_l_candidate:
            query = build_source_channel_query(
                donor=donor,
                block=block,
                channel=Channel.L,
                signals=(),
                score=0.0,
                freq=freq,
                query_context=query_context,
            )
            if isinstance(query, SearchQuery):
                queries.append(query)

    queries.sort(key=lambda query: (
        query.physical_page,
        blocks[query.block_id].block_index,
        query.sentence_ordinal,
        (Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L).index(query.primary_channel),
        query.subtype or "",
        query.query_id,
    ))
    selected, shortfalls, dedup_metrics, selection_rejected = select_query_pool(
        document, tuple(queries)
    )
    for reason, count in selection_rejected:
        rejected[reason] = rejected.get(reason, 0) + count
    for query in selected:
        retained[query.primary_channel] += 1
    attributed = {channel: 0 for channel in Channel if channel != Channel.D}
    for query in selected:
        for channel in query.attributed_channels:
            if channel != Channel.D:
                attributed[channel] += 1
    channel_order = tuple(channel for channel in Channel if channel != Channel.D)
    calque_metrics, section_calque_metrics = _compute_metrics_from_analysis(
        document, calque_analysis
    )
    result = SearchResult(
        document=document,
        algo_version=ALGO_VERSION,
        dictionary_version=DICT_VERSION,
        queries=selected,
        shortfalls=shortfalls,
        signal_hits=tuple(signal_hits),
        calque_metrics=calque_metrics,
        candidate_metrics=CandidateMetrics(
            generated_by_channel=tuple((channel, generated[channel]) for channel in channel_order),
            retained_primary_by_channel=tuple((channel, retained[channel]) for channel in channel_order),
            attributed_by_channel=tuple((channel, attributed[channel]) for channel in channel_order),
            rejected_by_reason=tuple(sorted(rejected.items())),
        ),
        dedup_metrics=dedup_metrics,
        warnings=(),
        section_calque_metrics=section_calque_metrics,
    )
    return result, tuple(queries)


def _has_non_a_primary_signals(document: SearchDocument) -> bool:
    blocks = {block.block_id: block for block in document.blocks}
    sections = {section.section_id: section for section in document.sections}
    for donor in document.sentences:
        evaluation = evaluate_candidate(donor, blocks[donor.block_id], sections.get(donor.section_id))
        if any(signal.channel in (Channel.N, Channel.B, Channel.K) for signal in evaluation.signals):
            return True
    return False


def _increment(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _increment_by(counts: dict[str, int], reason: str, count: int) -> None:
    counts[reason] = counts.get(reason, 0) + count


def select_query_pool(
    document: SearchDocument,
    pool: tuple[SearchQuery, ...],
) -> tuple[tuple[SearchQuery, ...], tuple[SectionShortfall, ...], DedupMetrics, tuple[tuple[str, int], ...]]:
    """Посекційна драбина §14: дедуплікація, слоти, T/L і чесний недобір."""

    blocks = {block.block_id: block for block in document.blocks}
    selected_all: list[SearchQuery] = []
    shortfalls: list[SectionShortfall] = []
    rejected: dict[str, int] = {}
    component_count = 0
    removed_count = 0
    merged_attributions = 0

    for section in document.sections:
        if section.kind not in CONTENT_SECTION_KINDS:
            continue
        section_rejected: dict[str, int] = {}
        section_pool = tuple(query for query in pool if query.section_id == section.section_id)
        base_pool = tuple(query for query in section_pool if query.primary_channel != Channel.L)
        base_winners, base_components, base_removed, base_merged = _deduplicate_queries(base_pool, blocks)
        component_count += base_components
        removed_count += base_removed
        merged_attributions += base_merged

        selected: list[SearchQuery] = []
        block_counts: dict[str, int] = {}
        page_counts: dict[int, int] = {}
        slot_pattern = (Channel.A, Channel.K, Channel.B, Channel.A, Channel.K,
                        Channel.B, Channel.A, Channel.K, Channel.B, Channel.A)
        for stage in (1, 2, 3):
            stage_pool = [
                query for query in base_winners
                if query.selection_stage == stage
                and query.primary_channel in (Channel.A, Channel.N, Channel.B, Channel.K)
                and query not in selected
            ]
            while stage_pool and len(selected) < TARGET_QUERIES_PER_SECTION:
                wanted = slot_pattern[len(selected)]
                matching = [query for query in stage_pool if query.primary_channel == wanted]
                candidates = matching or stage_pool
                candidate = min(candidates, key=lambda query: _selection_key(query, blocks))
                stage_pool.remove(candidate)
                if _within_limits(candidate, block_counts, page_counts, MAX_PER_BLOCK, MAX_PER_PAGE):
                    _accept(candidate, selected, block_counts, page_counts)
                else:
                    _increment(section_rejected, "diversity_limit")

        t_pool = sorted(
            (query for query in base_winners if query.primary_channel == Channel.T),
            key=lambda query: _selection_key(query, blocks),
        )
        _take_with_limits(
            t_pool, selected, block_counts, page_counts,
            TARGET_QUERIES_PER_SECTION, MAX_PER_BLOCK, MAX_PER_PAGE, section_rejected,
        )

        covered_subsections = {
            _subsection_key(blocks[query.block_id]) for query in selected
        }
        active_l = tuple(
            query for query in section_pool
            if query.primary_channel == Channel.L
            and _subsection_key(blocks[query.block_id]) not in covered_subsections
        )
        inactive_l = sum(
            query.primary_channel == Channel.L for query in section_pool
        ) - len(active_l)
        if inactive_l:
            _increment_by(section_rejected, "l_subsection_already_covered", inactive_l)
        l_without_base_edges = tuple(
            query for query in active_l
            if not any(_queries_are_duplicates(query, base, blocks) for base in base_winners)
        )
        rejected_l_edges = len(active_l) - len(l_without_base_edges)
        if rejected_l_edges:
            _increment_by(section_rejected, "l_duplicate_of_base", rejected_l_edges)
        l_winners, l_components, l_removed, l_merged = _deduplicate_queries(l_without_base_edges, blocks)
        component_count += l_components
        removed_count += l_removed + rejected_l_edges
        merged_attributions += l_merged
        l_pool = sorted(l_winners, key=lambda query: _selection_key(query, blocks))
        _take_with_limits(
            l_pool, selected, block_counts, page_counts,
            TARGET_QUERIES_PER_SECTION, MAX_PER_BLOCK, MAX_PER_PAGE, section_rejected,
        )

        if len(selected) < TARGET_QUERIES_PER_SECTION:
            remaining_tl = [
                query for query in (*t_pool, *l_pool) if query not in selected
            ]
            _take_with_limits(
                remaining_tl, selected, block_counts, page_counts,
                TARGET_QUERIES_PER_SECTION,
                RELAXED_MAX_PER_BLOCK, RELAXED_MAX_PER_PAGE, section_rejected,
            )

        if section.kind == SectionKind.INTRO and len(selected) >= TARGET_QUERIES_PER_SECTION:
            extras = sorted(
                (
                    query for query in base_winners
                    if query.primary_channel == Channel.N and query not in selected
                    and query.selection_stage in (1, 2, 3)
                ),
                key=lambda query: _selection_key(query, blocks),
            )
            for query in extras:
                if len(selected) >= MAX_QUERIES_PER_SECTION:
                    break
                if _within_limits(query, block_counts, page_counts, MAX_PER_BLOCK, MAX_PER_PAGE):
                    _accept(query, selected, block_counts, page_counts)

        post_dedup = len(base_winners) + len(l_winners)
        if not selected and post_dedup:
            fallback = min((*base_winners, *l_winners), key=lambda query: _selection_key(query, blocks))
            _accept(fallback, selected, block_counts, page_counts)
        selected_all.extend(selected)

        if len(selected) < TARGET_QUERIES_PER_SECTION:
            section_donors = tuple(
                donor for donor in document.sentences if donor.section_id == section.section_id
            )
            eligible_donors = {query.donor_id for query in section_pool}
            eligible_pre = len(base_pool) + len(active_l)
            normative_count = sum(
                len(normative_marker_ids(donor.raw_text)) >= 2
                and not any(
                    query.donor_id == donor.donor_id
                    and query.primary_channel in (Channel.A, Channel.N, Channel.B, Channel.K)
                    for query in section_pool
                )
                for donor in section_donors
            )
            normative_ratio = normative_count / len(section_donors) if section_donors else 0.0
            primary = _shortfall_primary_reason(
                document,
                section,
                generated_window_count=len(section_pool),
                eligible_pre_dedup_count=eligible_pre,
                post_dedup_count=post_dedup,
                actual=len(selected),
            )
            contributing: list[ShortfallReason] = []
            if section.coverage_ratio < 0.9:
                contributing.append(ShortfallReason.PARTIAL_COVERAGE)
            if normative_ratio >= NORMATIVE_HEAVY_RATIO:
                contributing.append(ShortfallReason.NORMATIVE_HEAVY)
            shortfalls.append(SectionShortfall(
                section_id=section.section_id,
                target=TARGET_QUERIES_PER_SECTION,
                actual=len(selected),
                author_words=section.author_words,
                raw_sentence_count=len(section_donors),
                eligible_donor_count=len(eligible_donors),
                generated_window_count=len(section_pool),
                eligible_pre_dedup_count=eligible_pre,
                post_dedup_count=post_dedup,
                coverage_ratio=section.coverage_ratio,
                normative_sentence_ratio=normative_ratio,
                primary_reason=primary,
                contributing_reasons=tuple(contributing),
                rejected_by_reason=tuple(sorted(section_rejected.items())),
            ))

        for reason, count in section_rejected.items():
            _increment_by(rejected, reason, count)

    return (
        tuple(selected_all),
        tuple(shortfalls),
        DedupMetrics(
            input_count=len(pool),
            component_count=component_count,
            removed_count=removed_count,
            merged_channel_attributions=merged_attributions,
        ),
        tuple(sorted(rejected.items())),
    )


def _deduplicate_queries(
    queries: tuple[SearchQuery, ...], blocks: dict[str, SearchBlock]
) -> tuple[tuple[SearchQuery, ...], int, int, int]:
    if not queries:
        return (), 0, 0, 0
    parent = list(range(len(queries)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    features = tuple(_duplicate_features(query.query_text) for query in queries)
    candidate_pairs = _duplicate_candidate_pairs(queries, features)
    for left, right in candidate_pairs:
        if _queries_are_duplicates_from_features(
            queries[left], queries[right], features[left], features[right]
        ):
            union(left, right)
    components: dict[int, list[SearchQuery]] = {}
    for index, query in enumerate(queries):
        components.setdefault(find(index), []).append(query)
    winners: list[SearchQuery] = []
    merged = 0
    for component in components.values():
        winner = min(component, key=lambda query: _winner_key(query, blocks))
        channels = tuple(
            channel for channel in (Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L)
            if any(channel in query.attributed_channels for query in component)
        )
        merged += max(len(channels) - len(winner.attributed_channels), 0)
        winners.append(replace(
            winner,
            attributed_channels=channels,
            reasons=tuple(sorted({reason for query in component for reason in query.reasons})),
            evidence_ids=tuple(sorted({evidence for query in component for evidence in query.evidence_ids})),
        ))
    winners.sort(key=lambda query: _winner_key(query, blocks))
    return tuple(winners), len(components), len(queries) - len(components), merged


def _duplicate_candidate_pairs(
    queries: tuple[SearchQuery, ...],
    features: tuple[
        tuple[tuple[str, ...], frozenset[str], frozenset[tuple[str, ...]]], ...
    ],
) -> tuple[tuple[int, int], ...]:
    """Точний prefix-фільтр кандидатів для Jaccard, 5-грам і пари A/B."""

    candidate_pairs: set[tuple[int, int]] = set()
    groups: dict[tuple[str, Language], list[int]] = {}
    donor_postings: dict[tuple[str, Language, str], list[int]] = {}
    for index, query in enumerate(queries):
        group = (query.section_id, query.query_language)
        groups.setdefault(group, []).append(index)
        if query.primary_channel in (Channel.A, Channel.B):
            donor_key = (*group, query.donor_id)
            posting = donor_postings.setdefault(donor_key, [])
            candidate_pairs.update((left, index) for left in posting)
            posting.append(index)

    for indices in groups.values():
        frequencies = Counter(
            token
            for index in indices
            for token in features[index][1]
        )
        prefix_postings: dict[str, list[int]] = {}
        run_postings: dict[tuple[str, ...], list[int]] = {}
        for index in indices:
            token_set = features[index][1]
            ordered_tokens = sorted(
                token_set,
                key=lambda token: (frequencies[token], token),
            )
            prefix_size = (
                len(ordered_tokens)
                - math.ceil(DEDUP_JACCARD_THRESHOLD * len(ordered_tokens))
                + 1
            ) if ordered_tokens else 0
            for token in ordered_tokens[:prefix_size]:
                posting = prefix_postings.setdefault(token, [])
                for left in posting:
                    left_size = len(features[left][1])
                    right_size = len(token_set)
                    if min(left_size, right_size) / max(left_size, right_size) >= DEDUP_JACCARD_THRESHOLD:
                        candidate_pairs.add((left, index))
                posting.append(index)
            for run in features[index][2]:
                posting = run_postings.setdefault(run, [])
                candidate_pairs.update((left, index) for left in posting)
                posting.append(index)

    return tuple(sorted(candidate_pairs))


def _queries_are_duplicates(
    left: SearchQuery, right: SearchQuery, blocks: dict[str, SearchBlock]
) -> bool:
    return _queries_are_duplicates_from_features(
        left,
        right,
        _duplicate_features(left.query_text),
        _duplicate_features(right.query_text),
    )


def _duplicate_features(
    text: str,
) -> tuple[tuple[str, ...], frozenset[str], frozenset[tuple[str, ...]]]:
    tokens = _significant_tokens(text)
    runs = frozenset(
        tokens[index:index + DEDUP_COMMON_RUN_WORDS]
        for index in range(len(tokens) - DEDUP_COMMON_RUN_WORDS + 1)
    )
    return tokens, frozenset(tokens), runs


def _queries_are_duplicates_from_features(
    left: SearchQuery,
    right: SearchQuery,
    left_features: tuple[tuple[str, ...], frozenset[str], frozenset[tuple[str, ...]]],
    right_features: tuple[tuple[str, ...], frozenset[str], frozenset[tuple[str, ...]]],
) -> bool:
    if left.section_id != right.section_id or left.query_language != right.query_language:
        return False
    if left.donor_id == right.donor_id and {left.primary_channel, right.primary_channel} == {Channel.A, Channel.B}:
        return True
    _, left_set, left_runs = left_features
    _, right_set, right_runs = right_features
    union = left_set | right_set
    jaccard = len(left_set & right_set) / len(union) if union else 0.0
    return jaccard >= DEDUP_JACCARD_THRESHOLD or bool(left_runs & right_runs)


def _significant_tokens(text: str) -> tuple[str, ...]:
    normalized = normalize_text(text)
    return tuple(
        token.normalized.casefold()
        for token in tokenize(text, normalized)
        if token.is_word and token.normalized.casefold() not in STOPWORDS
        and any(character.isalpha() for character in token.normalized)
    )


def _has_common_run(left: tuple[str, ...], right: tuple[str, ...], size: int) -> bool:
    if len(left) < size or len(right) < size:
        return False
    runs = {left[index:index + size] for index in range(len(left) - size + 1)}
    return any(right[index:index + size] in runs for index in range(len(right) - size + 1))


def _winner_key(query: SearchQuery, blocks: dict[str, SearchBlock]):
    channel_order = (Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L)
    return (
        query.selection_stage,
        -query.rank_score,
        -query.score,
        channel_order.index(query.primary_channel),
        query.physical_page,
        blocks[query.block_id].block_index,
        query.sentence_ordinal,
        query.query_id,
    )


def _selection_key(query: SearchQuery, blocks: dict[str, SearchBlock]):
    return (
        -query.rank_score,
        -query.score,
        query.physical_page,
        blocks[query.block_id].block_index,
        query.sentence_ordinal,
        query.query_id,
    )


def _within_limits(query, block_counts, page_counts, block_limit, page_limit) -> bool:
    return block_counts.get(query.block_id, 0) < block_limit and page_counts.get(query.physical_page, 0) < page_limit


def _accept(query, selected, block_counts, page_counts) -> None:
    selected.append(query)
    block_counts[query.block_id] = block_counts.get(query.block_id, 0) + 1
    page_counts[query.physical_page] = page_counts.get(query.physical_page, 0) + 1


def _take_with_limits(pool, selected, block_counts, page_counts, target, block_limit, page_limit, rejected) -> None:
    for query in pool:
        if len(selected) >= target:
            break
        if query in selected:
            continue
        if _within_limits(query, block_counts, page_counts, block_limit, page_limit):
            _accept(query, selected, block_counts, page_counts)
        else:
            _increment(rejected, "diversity_limit")


def _subsection_key(block: SearchBlock) -> tuple[str, ...]:
    return block.heading_path or (block.section_id,)


def _shortfall_primary_reason(document, section, *, generated_window_count, eligible_pre_dedup_count, post_dedup_count, actual):
    if document.body_biblio_confidence == Confidence.LOW:
        return ShortfallReason.SECTION_UNRESOLVED
    if section.author_words == 0:
        return ShortfallReason.NO_EXTRACTABLE_BODY
    if generated_window_count == 0:
        return ShortfallReason.NO_VALID_WINDOWS
    if eligible_pre_dedup_count < TARGET_QUERIES_PER_SECTION:
        return ShortfallReason.INSUFFICIENT_QUALITY
    if post_dedup_count < TARGET_QUERIES_PER_SECTION:
        return ShortfallReason.DEDUPLICATION_REDUCED
    return ShortfallReason.DIVERSITY_LIMITS


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
    word_tokens: list[SearchToken],
    start_idx: int,
    end_idx: int,
    raw_text: str,
    freq: dict[str, int],
    *,
    context: _DonorQueryContext | None = None,
) -> float:
    context = context or _build_query_context(
        raw_text,
        normalize_text(raw_text),
        word_tokens,
        freq,
    )
    score = SIGNAL_COVERAGE_BONUS + (
        context.token_bonus_prefix[end_idx] - context.token_bonus_prefix[start_idx]
    )
    window_text = raw_text[word_tokens[start_idx].raw_start : word_tokens[end_idx - 1].raw_end]
    cache_key = (start_idx, end_idx)
    penalty = context.normative_penalty_cache.get(cache_key)
    if penalty is None:
        penalty = NORMATIVE_PENALTY * len(normative_marker_ids(window_text))
        context.normative_penalty_cache[cache_key] = penalty
    score -= penalty
    return score


def _select_best_window(
    word_tokens: list[SearchToken],
    signal_raw_spans: list[tuple[int, int]],
    raw_text: str,
    freq: dict[str, int],
    *,
    context: _DonorQueryContext | None = None,
) -> tuple[int, int, float] | None:
    context = context or _build_query_context(
        raw_text,
        normalize_text(raw_text),
        word_tokens,
        freq,
    )
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
                        candidates[key] = _score_window(
                            word_tokens,
                            start_idx,
                            end_idx,
                            raw_text,
                            freq,
                            context=context,
                        )
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
    word_tokens: list[SearchToken],
    raw_text: str,
    block_id: str,
    physical_page: int,
    sentence_base: int,
    *,
    freq: dict[str, int] | None = None,
    query_context: _DonorQueryContext | None = None,
) -> tuple[str, SourceSpan, bool]:
    """
    Стійкий якір §15: 8–15 послідовних чистих вихідних слів з перевагою
    рідкісних форм, прізвищ і чисел. За відсутності вікна — весь донор.
    """
    n = len(word_tokens)
    if n < ANCHOR_MIN_WORDS:
        text = raw_text
        source = SourceSpan(
            parts=(RawSpan(block_id, physical_page, sentence_base, sentence_base + len(raw_text)),)
        )
        return text, source, True
    context = query_context or _build_query_context(
        raw_text,
        normalize_text(raw_text),
        word_tokens,
        freq or {},
    )
    proper_indexes = context.proper_name_indexes
    candidates: list[tuple[int, int, int, int]] = []
    for start in range(n):
        for size in range(ANCHOR_MIN_WORDS, ANCHOR_MAX_WORDS + 1):
            end = start + size
            if end > n:
                break
            window = word_tokens[start:end]
            if any(_anchor_token_is_unstable(token, raw_text) for token in window):
                continue
            score = sum(
                (3 if index in proper_indexes else 0)
                + (3 if context.rare_form_flags[index] else 0)
                + (2 if _is_number_token(token) else 0)
                for index, token in enumerate(word_tokens[start:end], start=start)
            )
            candidates.append((-score, -size, start, end))
    if not candidates:
        return raw_text, SourceSpan((
            RawSpan(block_id, physical_page, sentence_base, sentence_base + len(raw_text)),
        )), True
    _, _, start, end = min(candidates)
    first_tok, last_tok = word_tokens[start], word_tokens[end - 1]
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


def _anchor_token_is_unstable(token: SearchToken, raw_text: str) -> bool:
    raw = raw_text[token.raw_start:token.raw_end]
    mixed = bool(_LATIN_RE.search(raw) and _CYRILLIC_RE.search(raw))
    return "�" in raw or mixed or bool(_JOINED_HYPHEN_RE.search(raw))


def _proper_name_indexes(
    tokens: list[SearchToken],
    raw_text: str,
    *,
    surname_evidence: tuple[SurnameEvidence, ...] | None = None,
) -> frozenset[int]:
    indexes: set[int] = set()
    for surname in (
        extract_surname_evidence(raw_text)
        if surname_evidence is None
        else surname_evidence
    ):
        for index, token in enumerate(tokens):
            if token.raw_start < surname.raw_end and token.raw_end > surname.raw_start:
                indexes.add(index)
    for index in range(1, len(tokens) - 1):
        if (
            tokens[index].raw.isalpha()
            and tokens[index + 1].raw.isalpha()
            and tokens[index].raw[:1].isupper()
            and tokens[index + 1].raw[:1].isupper()
        ):
            indexes.update((index, index + 1))
    return frozenset(indexes)


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
