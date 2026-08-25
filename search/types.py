"""
search/types.py
Строгі контракти даних для режиму ?mode=search (PLAN_SEARCH.md, §4).

За межею JSON-декодера (search/state.py) неформальні dict не передаються:
усі публічні структури — це заморожені dataclass або Enum, описані тут.
Розширення полів метрик відбувається лише версіонованою моделлю.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal


# ---------------------------------------------------------------------------
# Перечислення
# ---------------------------------------------------------------------------

class SectionKind(Enum):
    TITLE = "title"
    TOC = "toc"
    ABSTRACT = "abstract"
    INTRO = "intro"
    CHAPTER = "chapter"
    CONCLUSIONS = "conclusions"
    BIBLIO = "biblio"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"


# Розділи, що дають квоту запитів (§6.1): лише вони мають SectionShortfall.
CONTENT_SECTION_KINDS: frozenset[SectionKind] = frozenset(
    {SectionKind.INTRO, SectionKind.CHAPTER, SectionKind.CONCLUSIONS}
)


class TextZone(Enum):
    AUTHOR_TEXT = "author_text"
    QUOTED_TEXT = "quoted_text"
    FOOTNOTE_TEXT = "footnote_text"
    BIBLIOGRAPHY = "bibliography"
    TOC = "toc"
    HEADER_FOOTER = "header_footer"
    UNCERTAIN = "uncertain"


# Пріоритет зон при перетині інтервалів (§4.1): перший у списку виграє.
ZONE_PRIORITY: tuple[TextZone, ...] = (
    TextZone.HEADER_FOOTER,
    TextZone.TOC,
    TextZone.BIBLIOGRAPHY,
    TextZone.FOOTNOTE_TEXT,
    TextZone.QUOTED_TEXT,
    TextZone.UNCERTAIN,
    TextZone.AUTHOR_TEXT,
)


class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PageTextState(Enum):
    TEXT_OK = "text_ok"
    LOW_TEXT = "low_text"
    NO_TEXT = "no_text"
    EXPECTED_SPARSE = "expected_sparse"


class Language(Enum):
    RU = "ru"
    UK = "uk"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Channel(Enum):
    A = "A"
    N = "N"
    B = "B"
    K = "K"
    T = "T"
    L = "L"
    D = "D"  # зарезервовано, вимкнено в MVP (§10.8)


# ---------------------------------------------------------------------------
# Координати та нормалізація
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawSpan:
    block_id: str
    physical_page: int
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class SourceSpan:
    parts: tuple[RawSpan, ...]  # впорядковані, не перетинаються


@dataclass(frozen=True)
class CharOrigin:
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class NormalizedText:
    text: str
    origins: tuple[CharOrigin, ...]  # len(origins) == len(text)


@dataclass(frozen=True)
class ZoneSpan:
    raw_start: int
    raw_end: int
    zone: TextZone
    confidence: Confidence
    detector: str


@dataclass(frozen=True)
class SearchToken:
    raw: str
    normalized: str
    raw_start: int
    raw_end: int
    normalized_start: int
    normalized_end: int
    is_word: bool


@dataclass(frozen=True)
class SearchBlock:
    block_id: str
    raw_text: str
    normalized: NormalizedText
    tokens: tuple[SearchToken, ...]
    section_id: str
    heading_path: tuple[str, ...]
    physical_page: int
    block_index: int
    zone_spans: tuple[ZoneSpan, ...]


# ---------------------------------------------------------------------------
# Сторінки та розділи
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageInfo:
    physical_page: int
    content_chars: int
    author_words: int
    large_raster_ratio: float
    extractable: bool
    state: PageTextState
    reason: str


@dataclass(frozen=True)
class SectionInfo:
    section_id: str
    kind: SectionKind
    ordinal: int | None
    heading: str
    block_start: int
    block_end: int
    physical_pages: tuple[int, ...]
    author_words: int
    expected_body_pages: int
    extractable_body_pages: int
    coverage_ratio: float
    confidence: Confidence


class SectionOverrideAction(Enum):
    SET_KIND = "set_kind"
    EXCLUDE_HEADING = "exclude_heading"


@dataclass(frozen=True)
class SectionOverride:
    action: SectionOverrideAction
    heading_block_id: str
    section_kind: SectionKind | None


# ---------------------------------------------------------------------------
# Донори речень, бібліографія, цитування
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SentenceDonor:
    donor_id: str
    block_id: str
    section_id: str
    sentence_ordinal: int
    occurrence_index: int
    source: SourceSpan
    raw_text: str
    normalized_text: str
    author_word_count: int


@dataclass(frozen=True)
class BibliographyEntry:
    entry_id: str
    ordinal: int | None
    raw_text: str
    source: SourceSpan
    title: str | None
    title_source: SourceSpan | None
    title_confidence: Confidence
    surnames: tuple[str, ...]
    year: int | None
    language: Language
    language_evidence: str


@dataclass(frozen=True)
class CitationMention:
    citation_id: str
    kind: str  # numeric / footnote / author_year / surname
    source: SourceSpan
    entry_ids: tuple[str, ...]
    confidence: Confidence


# ---------------------------------------------------------------------------
# Сигнали каналів і запити
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalHit:
    evidence_id: str
    channel: Channel
    rule_id: str
    source: SourceSpan
    zone: TextZone
    score: float
    reason: str


class QueryPartOrigin(Enum):
    SOURCE_PHRASE = "source_phrase"
    CALQUE_RULE = "calque_rule"
    RU_REFERENCE = "ru_reference"
    SURNAME_TRANSLITERATION = "surname_transliteration"
    LITERAL_NUMBER = "literal_number"
    SYSTEM_LITERAL = "system_literal"


@dataclass(frozen=True)
class QueryPart:
    text: str
    origin: QueryPartOrigin
    origin_id: str | None
    source: SourceSpan | None


@dataclass(frozen=True)
class SearchQuery:
    donor_id: str
    query_id: str
    block_id: str
    section_id: str
    sentence_ordinal: int
    primary_channel: Channel
    attributed_channels: tuple[Channel, ...]
    subtype: str | None
    query_language: Language
    selection_stage: Literal[1, 2, 3, 4, 5]  # >=4, >=3, >=2, T, L
    query_text: str
    parts: tuple[QueryPart, ...]
    donor_text: str
    donor_source: SourceSpan
    pdf_anchor: str
    pdf_anchor_source: SourceSpan
    physical_page: int
    score: float
    rank_score: float
    evidence_ids: tuple[str, ...]
    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Недобір
# ---------------------------------------------------------------------------

class ShortfallReason(Enum):
    SECTION_UNRESOLVED = "section_unresolved"
    NO_EXTRACTABLE_BODY = "no_extractable_body"
    NO_VALID_WINDOWS = "no_valid_windows"
    INSUFFICIENT_QUALITY = "insufficient_quality_candidates"
    DEDUPLICATION_REDUCED = "deduplication_reduced_pool"
    DIVERSITY_LIMITS = "diversity_limits"
    PARTIAL_COVERAGE = "partial_text_coverage"
    NORMATIVE_HEAVY = "normative_heavy"


@dataclass(frozen=True)
class SectionShortfall:
    section_id: str
    target: int
    actual: int
    author_words: int
    raw_sentence_count: int
    eligible_donor_count: int
    generated_window_count: int
    eligible_pre_dedup_count: int
    post_dedup_count: int
    coverage_ratio: float
    normative_sentence_ratio: float
    primary_reason: ShortfallReason
    contributing_reasons: tuple[ShortfallReason, ...]
    rejected_by_reason: tuple[tuple[str, int], ...]


# ---------------------------------------------------------------------------
# Документ і результат
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchDocument:
    document_sha256: str
    parser_version: str
    n_pages: int
    pages: tuple[PageInfo, ...]
    expected_body_pages: int
    extractable_body_pages: int
    coverage_ratio: float
    blocks: tuple[SearchBlock, ...]
    sections: tuple[SectionInfo, ...]
    sentences: tuple[SentenceDonor, ...]
    bibliography: tuple[BibliographyEntry, ...]
    citations: tuple[CitationMention, ...]
    body_biblio_confidence: Confidence
    applied_overrides: tuple[SectionOverride, ...]


@dataclass(frozen=True)
class CalqueMetrics:
    author_words: int
    tier1_hits: int
    tier2_hits: int
    tier3_hits: int
    tier1_density: float
    excluded_zone_hits: tuple[tuple[TextZone, int], ...]


@dataclass(frozen=True)
class CandidateMetrics:
    generated_by_channel: tuple[tuple[Channel, int], ...]
    retained_primary_by_channel: tuple[tuple[Channel, int], ...]
    attributed_by_channel: tuple[tuple[Channel, int], ...]
    rejected_by_reason: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class DedupMetrics:
    input_count: int
    component_count: int
    removed_count: int
    merged_channel_attributions: int


@dataclass(frozen=True)
class SearchResult:
    document: SearchDocument
    algo_version: str
    dictionary_version: str
    queries: tuple[SearchQuery, ...]
    shortfalls: tuple[SectionShortfall, ...]
    signal_hits: tuple[SignalHit, ...]
    calque_metrics: CalqueMetrics
    candidate_metrics: CandidateMetrics
    dedup_metrics: DedupMetrics
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Реєстр рушіїв (§16) — тип живе тут, наповнення в search/engines.py
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EngineSpec:
    code: str
    label: str
    channels: frozenset[Channel]
    home_url: str
    query_url_template: str | None
    max_query_chars: int
    warning: str | None
    verified_on: date | None
    active_prefill: bool
