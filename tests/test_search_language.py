"""Модульні тести визначення мови бібліографії (PLAN_SEARCH.md, §9)."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

import pytest

from search.language import (
    LANGUAGE_ALGO_VERSION,
    RU_FUNCTION_WORDS,
    RU_SPELLING_FORMS,
    RU_TABLES_VERSION,
    annotate_bibliography,
    bibliography_language_stats,
    detect_language,
    reliable_ru_content_words,
)
from search.types import BibliographyEntry, Confidence, Language, RawSpan, SourceSpan


def _entry(ordinal: int | None, text: str) -> BibliographyEntry:
    source = SourceSpan((RawSpan("block", 1, 0, len(text)),))
    return BibliographyEntry(
        entry_id=f"id-{ordinal}-{len(text)}",
        ordinal=ordinal,
        raw_text=text,
        source=source,
        title=text,
        title_source=source,
        title_confidence=Confidence.MEDIUM,
        surnames=(),
        year=2000,
        language=Language.UNKNOWN,
        language_evidence="pending",
    )


def test_versions_are_explicit_and_tables_are_immutable() -> None:
    assert LANGUAGE_ALGO_VERSION.startswith("language-")
    assert RU_TABLES_VERSION.startswith("ru-signals-")
    assert isinstance(RU_FUNCTION_WORDS, frozenset)
    assert isinstance(RU_SPELLING_FORMS, frozenset)


@pytest.mark.parametrize("shared", ["право", "система", "проблема", "форма", "метод"])
def test_shared_ukrainian_forms_are_not_positive_ru_signals(shared: str) -> None:
    assert shared not in RU_FUNCTION_WORDS
    assert shared not in RU_SPELLING_FORMS
    assert detect_language(f"{shared} держави").language == Language.UNKNOWN


def test_detection_evidence_names_the_exact_positive_signal() -> None:
    spelling = detect_language("Теория права")
    exclusive = detect_language("Объём исследования")

    assert spelling.ru_signals == ("spelling:теория",)
    assert "spelling:теория" in spelling.evidence
    assert exclusive.ru_signals == ("exclusive:объём",)


def test_reliable_word_offsets_point_back_to_source_text() -> None:
    text = "Вопросы теории и объёма правового исследования"

    words = reliable_ru_content_words(text)

    assert words
    for item in words:
        source = text[item.start : item.end]
        assert unicodedata.normalize("NFKC", source).casefold() == item.word


def test_function_signal_does_not_license_ambiguous_content_words() -> None:
    assert detect_language("Право и государство").language == Language.RU
    assert reliable_ru_content_words("Право и государство") == ()


def test_annotation_is_idempotent() -> None:
    original = (_entry(1, "Теория права"), _entry(2, "Теорія права"))
    once = annotate_bibliography(original)

    assert annotate_bibliography(once) == once


@pytest.mark.parametrize(
    "entries",
    [
        (_entry(2, "Теория права"), _entry(3, "Теория права")),
        (_entry(1, "Теория права"), _entry(1, "Теория права")),
        (_entry(1, "Теория права"), _entry(2, "Теория права"), _entry(99, "Теория права")),
    ],
)
def test_noncoherent_numbering_is_reported(entries) -> None:
    stats = bibliography_language_stats(entries, Confidence.HIGH)
    assert stats.sequentially_numbered is False
    assert stats.show_ru_percentage is False
    assert "not_sequentially_numbered" in stats.reasons


def test_empty_statistics_are_explicit_and_never_divide_by_zero() -> None:
    stats = bibliography_language_stats((), Confidence.HIGH)

    assert stats.total == 0
    assert stats.ru_ratio is None
    assert stats.coverage_ratio is None
    assert stats.show_ru_percentage is False
    assert "no_entries" in stats.reasons


def test_statistics_reclassify_raw_text_instead_of_trusting_placeholder_field() -> None:
    placeholder = replace(_entry(1, "Теория права"), language=Language.UNKNOWN)

    stats = bibliography_language_stats((placeholder,), Confidence.HIGH)

    assert stats.ru == 1
    assert stats.unknown == 0
