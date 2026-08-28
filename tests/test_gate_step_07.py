"""Незалежний шлюз кроку 7 за пакетом ``steps/step-07.md``."""

from __future__ import annotations

from dataclasses import replace

import pytest

from search.language import (
    MAX_UNCERTAIN_RATIO,
    MIN_BIBLIOGRAPHY_COVERAGE,
    MIN_CYRILLIC_WORDS,
    annotate_bibliography,
    bibliography_language_stats,
    classify_language,
    detect_language,
    reliable_ru_content_words,
)
from search.types import BibliographyEntry, Confidence, Language, RawSpan, SourceSpan


def _entry(ordinal: int | None, text: str) -> BibliographyEntry:
    source = SourceSpan((RawSpan("b", 1, 0, len(text)),))
    return BibliographyEntry(
        entry_id=f"entry-{ordinal}",
        ordinal=ordinal,
        raw_text=text,
        source=source,
        title=text,
        title_source=source,
        title_confidence=Confidence.HIGH,
        surnames=(),
        year=None,
        language=Language.UNKNOWN,
        language_evidence="not_evaluated_until_step_7",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Объём научного исследования", Language.RU),
        ("Теорія сучасного права", Language.UK),
        ("Теорія объёма исследования", Language.MIXED),
        ("Теория права", Language.RU),
        ("Право и государство", Language.RU),
        ("Право держави", Language.UNKNOWN),
        ("Theory of law", Language.UNKNOWN),
    ],
)
def test_gate_classifies_manual_language_examples(text: str, expected: Language) -> None:
    assert classify_language(text) == expected


def test_gate_short_function_word_fragment_stays_unknown() -> None:
    detection = detect_language("и")
    assert detection.language == Language.UNKNOWN
    assert detection.cyrillic_word_count < MIN_CYRILLIC_WORDS


def test_gate_nfkc_and_casefold_are_deterministic() -> None:
    assert detect_language("ТЕОРИЯ ПРАВА") == detect_language("Теория права")
    assert detect_language("Объём исследования") == detect_language("Объём исследования")


def test_gate_annotation_changes_only_language_fields() -> None:
    original = _entry(1, "Теория права")
    annotated = annotate_bibliography((original,))[0]

    expected = replace(
        original,
        language=Language.RU,
        language_evidence=detect_language(original.raw_text).evidence,
    )
    assert annotated == expected
    assert annotated.entry_id == original.entry_id
    assert annotated.source == original.source


def test_gate_reliable_ru_words_require_their_own_positive_signal() -> None:
    words = reliable_ru_content_words("Теория и право объёма")

    assert [item.word for item in words] == ["теория", "объёма"]
    assert all(item.start < item.end and item.signal for item in words)


@pytest.mark.parametrize("text", ["Теорія права", "Теорія объёма", "Право держави"])
def test_gate_non_ru_records_supply_no_k2_words(text: str) -> None:
    assert reliable_ru_content_words(text) == ()


def test_gate_language_statistics_use_all_records_as_denominator() -> None:
    entries = tuple(
        [_entry(i, "Теория права") for i in range(1, 8)]
        + [_entry(8, "Теорія права"), _entry(9, "Історія права"), _entry(10, "Право держави")]
    )

    stats = bibliography_language_stats(entries, Confidence.HIGH)

    assert (stats.ru, stats.uk, stats.mixed, stats.unknown, stats.total) == (7, 2, 0, 1, 10)
    assert stats.ru_ratio == pytest.approx(0.7)
    assert (stats.mixed + stats.unknown) / stats.total == MAX_UNCERTAIN_RATIO
    assert stats.show_ru_percentage is True


def test_gate_one_allowed_gap_gives_ninety_percent_coverage() -> None:
    entries = tuple(_entry(i, "Теория права") for i in (*range(1, 9), 10))

    stats = bibliography_language_stats(entries, Confidence.MEDIUM)

    assert stats.expected_count == 10
    assert stats.coverage_ratio == pytest.approx(MIN_BIBLIOGRAPHY_COVERAGE)
    assert stats.sequentially_numbered is True
    assert stats.show_ru_percentage is True


@pytest.mark.parametrize(
    ("entries", "confidence", "reason"),
    [
        (tuple(_entry(None, "Теория права") for _ in range(3)), Confidence.HIGH, "not_sequentially_numbered"),
        (tuple(_entry(i, "Теория права") for i in (1, 2, 99)), Confidence.HIGH, "not_sequentially_numbered"),
        (tuple(_entry(i, "Теория права") for i in range(1, 11)), Confidence.LOW, "bibliography_boundary_low_confidence"),
    ],
)
def test_gate_invalid_numbering_or_boundary_hides_percentage(entries, confidence, reason) -> None:
    stats = bibliography_language_stats(entries, confidence)
    assert stats.show_ru_percentage is False
    assert reason in stats.reasons


def test_gate_more_than_ten_percent_uncertain_hides_percentage() -> None:
    entries = tuple(
        [_entry(i, "Теория права") for i in range(1, 9)]
        + [_entry(9, "Право держави"), _entry(10, "Загальне право")]
    )
    stats = bibliography_language_stats(entries, Confidence.HIGH)

    assert stats.unknown == 2
    assert stats.show_ru_percentage is False
    assert "uncertain_above_10_percent" in stats.reasons


def test_gate_zero_ru_is_visible_but_not_presented_as_positive_evidence() -> None:
    entries = tuple(_entry(i, "Теорія українського права") for i in range(1, 11))
    stats = bibliography_language_stats(entries, Confidence.HIGH)

    assert stats.ru == 0
    assert stats.ru_ratio == 0.0
    assert stats.zero_ru_is_evidence is False
