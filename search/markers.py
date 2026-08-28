"""
search/markers.py
Виробничі детектори каналів A, N, B, K, T, L, оцінка кандидатів і явні
причини відсіву. Специфікація — PLAN_SEARCH.md, §§10–12 та §22, крок 9.

Модуль не будує текстів запитів і не застосовує квоти: це контракт кроків
10–11. Усі координати `CandidateSignal` є півінтервалами у вихідному тексті
речення; контекстний N-сигнал за заголовком покриває все речення.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from search.calques import find_calques_with_rejections, resolve_zone, tier2_is_scorable
from search.normalization import WORD_TOKEN_RE, normalize_text, tokenize
from search.types import (
    CONTENT_SECTION_KINDS,
    Channel,
    SearchBlock,
    SearchDocument,
    SectionInfo,
    SectionKind,
    SentenceDonor,
    TextZone,
)

MARKERS_VERSION = "search-markers-2026-08-29"
STOPWORDS_VERSION = "search-stopwords-2026-08-29"
NORMATIVE_MARKERS_VERSION = "normative-markers-2026-08-29"

CHANNEL_A_SIGNAL_SCORE = 2.0
CHANNEL_A_MAX_SCORE = 6.0
CHANNEL_N_SCORE = 4.0
CHANNEL_B_PRIMARY_SCORE = 2.0
CHANNEL_B_BONUS_SCORE = 1.0
CHANNEL_B_MAX_SCORE = 4.0
CHANNEL_K_TIER1_SCORE = 3.0
CHANNEL_K_TIER1_MAX_SCORE = 6.0
CHANNEL_K_TIER2_SCORE = 1.0
CHANNEL_K_TIER2_MAX_SCORE = 3.0

INTRO_CONCLUSIONS_MULTIPLIER = 1.5
NOVELTY_MULTIPLIER = 2.0
NORMATIVE_MULTIPLIER = 0.2
NEUTRAL_MULTIPLIER = 1.0

EMPIRICAL_MAX_INTERVENING_WORDS = 4
RARE_MIN_LETTERS = 4
RARE_MAX_FREQUENCY = 2
LONG_MIN_WORDS = 18
NORMATIVE_MIN_DISTINCT_MARKERS = 2

REASON_NOT_AUTHOR_TEXT = "not_author_text"
REASON_SECTION_UNRESOLVED = "section_unresolved"
REASON_SECTION_NOT_CONTENT = "section_not_content_kind"
REASON_CALQUE_CONTEXT = "calque_context_rejected"
REASON_K_EXCLUDED_ZONE = "k_excluded_zone"
REASON_K_TIER2_WITHOUT_TIER1 = "k_tier2_without_tier1"
REASON_K_TIER3_STATISTICAL = "k_tier3_statistical_only"
REASON_SCORE_CAP = "channel_score_cap"
REASON_NORMATIVE_HEAVY = "normative_heavy"


@dataclass(frozen=True)
class ChannelASignal:
    """Зворотно сумісний A-збіг у координатах нормалізованого речення."""

    rule_id: str
    start: int
    end: int


@dataclass(frozen=True)
class CandidateSignal:
    """Один сигнал каналу у вихідних координатах тексту кандидата."""

    channel: Channel
    rule_id: str
    raw_start: int
    raw_end: int
    score: float
    reason: str


@dataclass(frozen=True)
class MarkerRejection:
    """Видима причина, через яку збіг або кандидат не дає бала."""

    reason: str
    rule_id: str | None
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class RareWordForm:
    """Рідкісна словоформа та її стабільна позиція у документі."""

    form: str
    frequency: int
    first_physical_page: int
    first_block_index: int
    first_raw_start: int


@dataclass(frozen=True)
class CandidateEvaluation:
    """Повна діагностика маркерів одного речення до побудови запиту."""

    signals: tuple[CandidateSignal, ...]
    rejections: tuple[MarkerRejection, ...]
    base_score: float
    section_multiplier: float
    novelty_multiplier: float
    normative_multiplier: float
    final_score: float
    normative_marker_ids: tuple[str, ...]
    normative_heavy: bool
    channel_t_candidate: bool
    channel_l_candidate: bool


_UK_LETTER_TAIL = "а-щьюяіїєґ'’-"
_A_STEMS: tuple[str, ...] = (
    "пропон", "вважа", "доцільн", "обґрунтован", "запропонован", "удосконален",
)
_A_STEM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(rf"\b{stem}[{_UK_LETTER_TAIL}]+", re.IGNORECASE | re.UNICODE),
        f"A.stem.{stem}",
    )
    for stem in _A_STEMS
)
_A_PHRASES: tuple[str, ...] = (
    "на нашу думку", "дійшли висновку", "у такій редакції",
)
_A_PHRASE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE | re.UNICODE), f"A.phrase.{i}")
    for i, phrase in enumerate(_A_PHRASES)
)
_NOVELTY_HEADING = "наукова новизна одержаних результатів"
_N_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bуперше\b", re.IGNORECASE | re.UNICODE), "N.first_time"),
    (re.compile(r"\bудосконалено\b", re.IGNORECASE | re.UNICODE), "N.improved"),
    (
        re.compile(r"\bнабуло\s+подальшого\s+розвитку\b", re.IGNORECASE | re.UNICODE),
        "N.further_developed",
    ),
)

_EMPIRICAL_STEMS: tuple[str, ...] = (
    "респондент", "опитан", "анкет", "вибірк", "справ", "вирок",
    "проваджен", "працівник", "особ", "документ", "матеріал", "досліджен",
)
_NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])", re.UNICODE)
_PERCENT_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?\s*%", re.UNICODE)
_CURRENCY_RE = re.compile(
    r"(?<![\w])(?:[$€₴]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:грн|грив(?:ня|ні|ень)|долар\w*|євро|USD|EUR|UAH))\b",
    re.IGNORECASE | re.UNICODE,
)
_MEASUREMENT_RE = re.compile(
    r"(?<![\w])\d+(?:[.,]\d+)?\s*(?:мм|см|км|м|м²|м3|м³|г|кг|т|мл|л|га|год(?:ин\w*)?)\b",
    re.IGNORECASE | re.UNICODE,
)
_DATE_RANGE_RE = re.compile(r"(?<!\d)(?:18|19|20)\d{2}\s*[–—-]\s*(?:18|19|20)\d{2}(?!\d)")

STOPWORDS: frozenset[str] = frozenset({
    "аби", "або", "адже", "але", "без", "би", "був", "була", "були", "було",
    "в", "від", "він", "вона", "вони", "воно", "для", "до", "з", "за", "і",
    "із", "й", "ми", "на", "над", "не", "ні", "однак", "по", "при", "про",
    "під", "та", "також", "те", "ти", "то", "той", "у", "цей", "ця", "це",
    "ці", "чи", "що", "як", "я", "є", "зі", "теж", "їх", "його", "її",
})

_NORMATIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("norm.article", re.compile(r"\bстатт(?:я|і|ю|ею)\s+\d+", re.IGNORECASE | re.UNICODE)),
    ("norm.part", re.compile(r"\bчастин(?:а|и|і|у|ою)\s+\d+", re.IGNORECASE | re.UNICODE)),
    ("norm.point", re.compile(r"\bпункт(?:у|ом|і|а)?\s+\d+", re.IGNORECASE | re.UNICODE)),
    ("norm.constitution", re.compile(r"\bКонституці(?:я|ї|ю|єю)\s+України\b", re.IGNORECASE | re.UNICODE)),
    ("norm.law", re.compile(r"\bЗакон(?:у|ом|і)?\s+України\b", re.IGNORECASE | re.UNICODE)),
    ("norm.code", re.compile(r"\b(?:кодекс\w*|КПК|ЦК|КК|КУпАП|КАС|ГПК|ЦПК|КЗпП)\b", re.IGNORECASE | re.UNICODE)),
)

_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")  # ru-data


def find_channel_a_signals(normalized_text: str) -> tuple[ChannelASignal, ...]:
    """Усі A-збіги у нормалізованому тексті за стабільним порядком."""

    matches: list[ChannelASignal] = []
    for pattern, rule_id in _A_STEM_PATTERNS + _A_PHRASE_PATTERNS:
        matches.extend(ChannelASignal(rule_id, m.start(), m.end()) for m in pattern.finditer(normalized_text))
    matches.extend(_a_token_window_signals(normalized_text))
    return tuple(sorted(matches, key=lambda signal: (signal.start, signal.end, signal.rule_id)))


def score_channel_a(signals: tuple[ChannelASignal, ...]) -> float:
    """Балл A: +2 за збіг, не більше +6 (§10.2)."""

    return min(len(signals) * CHANNEL_A_SIGNAL_SCORE, CHANNEL_A_MAX_SCORE)


def channel_a_signals(raw_text: str) -> tuple[CandidateSignal, ...]:
    """A-збіги у вихідних координатах речення з урахуванням стелі бала."""

    normalized = normalize_text(raw_text)
    result: list[CandidateSignal] = []
    scored = 0.0
    for signal in find_channel_a_signals(normalized.text):
        raw_start, raw_end = _raw_bounds(normalized.origins, signal.start, signal.end)
        score = CHANNEL_A_SIGNAL_SCORE if scored < CHANNEL_A_MAX_SCORE else 0.0
        scored += score
        result.append(CandidateSignal(Channel.A, signal.rule_id, raw_start, raw_end, score, signal.rule_id))
    return tuple(result)


def find_channel_n_signals(raw_text: str, headings: Iterable[str] = ()) -> tuple[CandidateSignal, ...]:
    """Один N-сигнал на речення: заголовок новизни або перший текстовий маркер."""

    if any(_normalized_heading(heading) == _NOVELTY_HEADING for heading in headings):
        return (CandidateSignal(Channel.N, "N.novelty_heading", 0, len(raw_text), CHANNEL_N_SCORE, "novelty_heading"),)
    normalized = normalize_text(raw_text)
    for pattern, rule_id in _N_PATTERNS:
        match = pattern.search(normalized.text)
        if match:
            raw_start, raw_end = _raw_bounds(normalized.origins, match.start(), match.end())
            return (CandidateSignal(Channel.N, rule_id, raw_start, raw_end, CHANNEL_N_SCORE, rule_id),)
    return ()


def find_channel_b_signals(raw_text: str) -> tuple[CandidateSignal, ...]:
    """Сигнали B з єдиним основним +2 і двома можливими бонусами +1."""

    normalized = normalize_text(raw_text)
    tokens = tuple(token for token in tokenize(raw_text, normalized) if token.is_word)
    empirical_indices = tuple(
        index
        for index, token in enumerate(tokens)
        if any(token.normalized.casefold().startswith(stem) for stem in _EMPIRICAL_STEMS)
    )
    numbers = tuple(_NUMBER_RE.finditer(normalized.text))
    strong = _first_match(normalized.text, (_PERCENT_RE, _CURRENCY_RE, _MEASUREMENT_RE))
    primary = strong
    if primary is None and empirical_indices:
        for number in numbers:
            number_index = _word_index_for_span(tokens, number.start(), number.end())
            if number_index is not None and any(
                max(abs(number_index - empirical) - 1, 0) <= EMPIRICAL_MAX_INTERVENING_WORDS
                for empirical in empirical_indices
            ):
                primary = number
                break
    if primary is None:
        return ()

    result: list[CandidateSignal] = []
    start, end = _raw_bounds(normalized.origins, primary.start(), primary.end())
    result.append(CandidateSignal(Channel.B, "B.empirical_value", start, end, CHANNEL_B_PRIMARY_SCORE, "empirical_value"))
    date_range = _DATE_RANGE_RE.search(normalized.text)
    if date_range:
        start, end = _raw_bounds(normalized.origins, date_range.start(), date_range.end())
        result.append(CandidateSignal(Channel.B, "B.date_range", start, end, CHANNEL_B_BONUS_SCORE, "date_range"))
    if len(numbers) >= 2:
        second = numbers[1]
        start, end = _raw_bounds(normalized.origins, second.start(), second.end())
        result.append(CandidateSignal(Channel.B, "B.second_number", start, end, CHANNEL_B_BONUS_SCORE, "second_number"))
    return _cap_signal_scores(tuple(result), CHANNEL_B_MAX_SCORE)


def find_channel_k_signals(
    block: SearchBlock,
    *,
    raw_start: int | None = None,
    raw_end: int | None = None,
) -> tuple[CandidateSignal, ...]:
    """Усі видимі K-збіги блока; бал мають лише дозволені AUTHOR_TEXT-рівні."""

    all_hits, _ = find_calques_with_rejections(block)
    tier2_allowed = tier2_is_scorable(all_hits)
    hits = tuple(
        hit
        for hit in all_hits
        if (raw_start is None or hit.raw_end > raw_start)
        and (raw_end is None or hit.raw_start < raw_end)
    )
    tier1_score = 0.0
    tier2_score = 0.0
    result: list[CandidateSignal] = []
    for hit in hits:
        score = 0.0
        reason = f"K.tier{hit.tier}"
        if hit.zone != TextZone.AUTHOR_TEXT:
            reason = REASON_K_EXCLUDED_ZONE
        elif hit.tier == 1:
            if tier1_score < CHANNEL_K_TIER1_MAX_SCORE:
                score = CHANNEL_K_TIER1_SCORE
                tier1_score += score
            else:
                reason = REASON_SCORE_CAP
        elif hit.tier == 2:
            if not tier2_allowed:
                reason = REASON_K_TIER2_WITHOUT_TIER1
            elif tier2_score < CHANNEL_K_TIER2_MAX_SCORE:
                score = CHANNEL_K_TIER2_SCORE
                tier2_score += score
            else:
                reason = REASON_SCORE_CAP
        else:
            reason = REASON_K_TIER3_STATISTICAL
        result.append(CandidateSignal(Channel.K, f"K.{hit.rule_id}", hit.raw_start, hit.raw_end, score, reason))
    return tuple(result)


def channel_k_rejections(block: SearchBlock) -> tuple[MarkerRejection, ...]:
    """Контекстні відмови виконуваного словника у спільному форматі маркерів."""

    _, rejected = find_calques_with_rejections(block)
    return tuple(
        MarkerRejection(f"{REASON_CALQUE_CONTEXT}:{item.reason}", item.rule_id, item.raw_start, item.raw_end)
        for item in rejected
    )


def rare_word_forms(document: SearchDocument) -> tuple[RareWordForm, ...]:
    """Рідкісні форми з включеного AUTHOR_TEXT у порядку §10.6."""

    occurrences: dict[str, list[tuple[int, int, int]]] = {}
    for block in sorted(document.blocks, key=lambda item: (item.physical_page, item.block_index, item.block_id)):
        for token in block.tokens:
            if not token.is_word or resolve_zone(block.zone_spans, token.raw_start, token.raw_end) != TextZone.AUTHOR_TEXT:
                continue
            form = token.normalized.casefold()
            if not _eligible_rare_form(form):
                continue
            occurrences.setdefault(form, []).append((block.physical_page, block.block_index, token.raw_start))
    result = [
        RareWordForm(form, len(positions), *positions[0])
        for form, positions in occurrences.items()
        if 1 <= len(positions) <= RARE_MAX_FREQUENCY
    ]
    return tuple(sorted(result, key=lambda item: (item.frequency, -len(item.form), item.first_physical_page, item.first_block_index, item.first_raw_start, item.form)))


def find_channel_t_signals(raw_text: str, rare_forms: Iterable[RareWordForm]) -> tuple[CandidateSignal, ...]:
    """Рідкісні форми, що реально присутні у реченні; T сам бала не додає."""

    allowed = {item.form for item in rare_forms}
    normalized = normalize_text(raw_text)
    result: list[CandidateSignal] = []
    for token in tokenize(raw_text, normalized):
        form = token.normalized.casefold()
        if token.is_word and form in allowed:
            result.append(CandidateSignal(Channel.T, f"T.{form}", token.raw_start, token.raw_end, 0.0, "rare_form"))
    return tuple(result)


def normative_marker_ids(raw_text: str) -> tuple[str, ...]:
    """Різні нормативні маркери у стабільному порядку версіонованого списку."""

    normalized = normalize_text(raw_text).text
    return tuple(rule_id for rule_id, pattern in _NORMATIVE_PATTERNS if pattern.search(normalized))


def is_normative_heavy(raw_text: str, semantic_signals: Iterable[CandidateSignal] = ()) -> bool:
    """Два різні нормативні маркери й повна відсутність A/N/B/K (§10.7)."""

    has_primary = any(signal.channel in (Channel.A, Channel.N, Channel.B, Channel.K) for signal in semantic_signals)
    return not has_primary and len(normative_marker_ids(raw_text)) >= NORMATIVE_MIN_DISTINCT_MARKERS


def is_channel_l_candidate(donor: SentenceDonor, *, author_text: bool, normative_heavy: bool) -> bool:
    """Базовий пул L; посекційний добір застосує крок 11."""

    return author_text and donor.author_word_count >= LONG_MIN_WORDS and not normative_heavy


def evaluate_candidate(
    donor: SentenceDonor,
    block: SearchBlock,
    section: SectionInfo | None,
    rare_forms: Iterable[RareWordForm] = (),
) -> CandidateEvaluation:
    """Детерміновано зібрати сигнали, відмови, множники та підсумковий бал."""

    headings = block.heading_path + ((section.heading,) if section is not None else ())
    signals = list(channel_a_signals(donor.raw_text))
    signals.extend(find_channel_n_signals(donor.raw_text, headings))
    signals.extend(find_channel_b_signals(donor.raw_text))
    donor_start, donor_end = _donor_block_bounds(donor, block.block_id)
    for signal in find_channel_k_signals(block, raw_start=donor_start, raw_end=donor_end):
        signals.append(CandidateSignal(
            signal.channel,
            signal.rule_id,
            max(signal.raw_start - donor_start, 0),
            min(signal.raw_end - donor_start, len(donor.raw_text)),
            signal.score,
            signal.reason,
        ))
    t_signals = find_channel_t_signals(donor.raw_text, rare_forms)
    signals.extend(t_signals)
    signals.sort(key=lambda item: (item.raw_start, item.raw_end, _channel_order(item.channel), item.rule_id))

    rejections = [
        item for item in channel_k_rejections(block)
        if item.raw_start < donor_end and item.raw_end > donor_start
    ]
    author_text = _donor_is_author_text(donor, block)
    if not author_text:
        rejections.append(MarkerRejection(REASON_NOT_AUTHOR_TEXT, None, 0, len(donor.raw_text)))
    if section is None:
        rejections.append(MarkerRejection(REASON_SECTION_UNRESOLVED, None, 0, len(donor.raw_text)))
    elif section.kind not in CONTENT_SECTION_KINDS:
        rejections.append(MarkerRejection(REASON_SECTION_NOT_CONTENT, None, 0, len(donor.raw_text)))

    semantic = tuple(signal for signal in signals if signal.channel in (Channel.A, Channel.N, Channel.B, Channel.K))
    normative_ids = normative_marker_ids(donor.raw_text)
    normative_heavy = author_text and not semantic and len(normative_ids) >= NORMATIVE_MIN_DISTINCT_MARKERS
    if normative_heavy:
        rejections.append(MarkerRejection(REASON_NORMATIVE_HEAVY, None, 0, len(donor.raw_text)))

    base_score = sum(signal.score for signal in signals)
    section_multiplier = (
        INTRO_CONCLUSIONS_MULTIPLIER
        if section is not None and section.kind in (SectionKind.INTRO, SectionKind.CONCLUSIONS)
        else NEUTRAL_MULTIPLIER
    )
    novelty_multiplier = NOVELTY_MULTIPLIER if _is_novelty_context(headings) else NEUTRAL_MULTIPLIER
    normative_multiplier = NORMATIVE_MULTIPLIER if normative_heavy else NEUTRAL_MULTIPLIER
    eligible = author_text and section is not None and section.kind in CONTENT_SECTION_KINDS
    final_score = base_score * section_multiplier * novelty_multiplier * normative_multiplier if eligible else 0.0

    rejections.sort(key=lambda item: (item.raw_start, item.raw_end, item.reason, item.rule_id or ""))
    return CandidateEvaluation(
        signals=tuple(signals),
        rejections=tuple(rejections),
        base_score=base_score,
        section_multiplier=section_multiplier,
        novelty_multiplier=novelty_multiplier,
        normative_multiplier=normative_multiplier,
        final_score=final_score,
        normative_marker_ids=normative_ids,
        normative_heavy=normative_heavy,
        channel_t_candidate=bool(t_signals),
        channel_l_candidate=is_channel_l_candidate(donor, author_text=author_text, normative_heavy=normative_heavy),
    )


def _normalized_heading(text: str) -> str:
    normalized = normalize_text(text).text.casefold()
    return " ".join(re.findall(r"[^\W\d_]+", normalized, re.UNICODE))


def _a_token_window_signals(normalized_text: str) -> tuple[ChannelASignal, ...]:
    words = tuple(
        match
        for match in WORD_TOKEN_RE.finditer(normalized_text)
        if any(character.isalpha() for character in match.group())
    )
    result: list[ChannelASignal] = []
    folded = tuple(match.group().casefold() for match in words)
    for index, word in enumerate(folded):
        if word == "під":
            for target_index in range(index + 2, min(index + 10, len(words))):
                if folded[target_index].startswith("розум"):
                    result.append(ChannelASignal(
                        "A.under_understand", words[index].start(), words[target_index].end()
                    ))
                    break
        if word == "нами":
            first = index + 1
            if first < len(words) and folded[first] == "було":
                first += 1
            for target_index in range(first, min(first + 3, len(words))):
                if folded[target_index] in ("опитано", "проаналізовано", "досліджено"):
                    result.append(ChannelASignal(
                        "A.we_surveyed", words[index].start(), words[target_index].end()
                    ))
                    break
    return tuple(result)


def _is_novelty_context(headings: Iterable[str]) -> bool:
    return any(_normalized_heading(heading) == _NOVELTY_HEADING for heading in headings)


def _raw_bounds(origins, start: int, end: int) -> tuple[int, int]:
    selected = origins[start:end]
    if not selected:
        return 0, 0
    return min(item.raw_start for item in selected), max(item.raw_end for item in selected)


def _first_match(text: str, patterns: Iterable[re.Pattern[str]]):
    matches = [match for pattern in patterns if (match := pattern.search(text)) is not None]
    return min(matches, key=lambda match: (match.start(), match.end())) if matches else None


def _word_index_for_span(tokens, start: int, end: int) -> int | None:
    for index, token in enumerate(tokens):
        if token.normalized_start < end and token.normalized_end > start:
            return index
    return None


def _cap_signal_scores(signals: tuple[CandidateSignal, ...], maximum: float) -> tuple[CandidateSignal, ...]:
    total = 0.0
    result: list[CandidateSignal] = []
    for signal in signals:
        score = min(signal.score, max(maximum - total, 0.0))
        total += score
        result.append(
            signal if score == signal.score else CandidateSignal(
                signal.channel, signal.rule_id, signal.raw_start, signal.raw_end, score, REASON_SCORE_CAP
            )
        )
    return tuple(result)


def _eligible_rare_form(form: str) -> bool:
    letters = sum(character.isalpha() for character in form)
    if letters < RARE_MIN_LETTERS or form in STOPWORDS or any(character.isdigit() for character in form):
        return False
    return not (_LATIN_RE.search(form) and _CYRILLIC_RE.search(form))


def _donor_block_bounds(donor: SentenceDonor, block_id: str) -> tuple[int, int]:
    parts = tuple(part for part in donor.source.parts if part.block_id == block_id)
    if not parts:
        return 0, 0
    return min(part.raw_start for part in parts), max(part.raw_end for part in parts)


def _donor_is_author_text(donor: SentenceDonor, block: SearchBlock) -> bool:
    parts = tuple(part for part in donor.source.parts if part.block_id == block.block_id)
    return bool(parts) and all(
        resolve_zone(block.zone_spans, part.raw_start, part.raw_end) == TextZone.AUTHOR_TEXT
        for part in parts
    )


def _channel_order(channel: Channel) -> int:
    return (Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L, Channel.D).index(channel)
