"""Типи даних конвеєра порівняння двох робіт."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TokenPart:
    line_index: int
    char_start: int
    char_end: int
    physical_page: int | None


@dataclass(frozen=True)
class CompareToken:
    raw: str
    normalized: str
    parts: tuple[TokenPart, ...]

    @property
    def physical_pages(self) -> tuple[int | None, ...]:
        return tuple(dict.fromkeys(part.physical_page for part in self.parts))


@dataclass(frozen=True)
class DiffSpan:
    start_token: int
    end_token: int
    operation: Literal["equal", "fuzzy", "replace", "insert", "delete"]


@dataclass
class TextSegment:
    a_start: int
    a_end: int
    b_start: int
    b_end: int
    matched: int
    fuzzy_matched: int
    len_a: int
    len_b: int
    coverage_a: float
    coverage_b: float
    similarity: float
    longest_verbatim: int
    kind: Literal["verbatim", "modified"]
    possibly_normative: bool
    possibly_boilerplate: bool
    status: Literal["accepted", "accepted_normative", "normative_only"]
    a_spans: tuple[DiffSpan, ...]
    b_spans: tuple[DiffSpan, ...]


@dataclass(frozen=True)
class ExcludedRange:
    start: int
    end: int
    reason: Literal["title_page", "toc", "bibliography"]


@dataclass
class BiblioMatchResult:
    parsed_a: bool
    parsed_b: bool
    entries_a: int
    entries_b: int
    common_exact: int
    common_near: int
    alphabetical_a: float
    alphabetical_b: float
    order_signal_applicable: bool
    order_runs: tuple[int, ...]


@dataclass
class ComparisonResult:
    segments: list[TextSegment]
    analyzed_tokens_a: int
    analyzed_tokens_b: int
    covered_tokens_a: int
    covered_tokens_b: int
    covered_tokens_a_strict: int
    covered_tokens_b_strict: int
    excluded_a: tuple[ExcludedRange, ...]
    excluded_b: tuple[ExcludedRange, ...]
    biblio: BiblioMatchResult | None
    analysis_complete: bool
    candidates_total: int
    candidates_processed: int


@dataclass
class PreparedDocument:
    tokens: list[CompareToken]
    all_tokens: list[CompareToken]
    excluded: tuple[ExcludedRange, ...]
    resembles_dissertation: bool
