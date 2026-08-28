"""
search/language.py
Визначення мови бібліографічного запису: RU / UK / MIXED / UNKNOWN,
надійні російські слова для K2 та статистика бібліографії.
Специфікація — PLAN_SEARCH.md, §9 (крок 7 таблиці §22).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from search.types import BibliographyEntry, Confidence, Language


LANGUAGE_ALGO_VERSION = "language-1.0"
RU_TABLES_VERSION = "ru-signals-1.0"

MIN_CYRILLIC_WORDS = 2
MAX_NUMBERING_MISSING_RATIO = 0.10
MIN_BIBLIOGRAPHY_COVERAGE = 0.90
MAX_UNCERTAIN_RATIO = 0.10

_RU_EXCLUSIVE = frozenset("ыэъё")
_UK_EXCLUSIVE = frozenset("іїєґ")

# Короткі слова, які відрізняються від нормативних українських відповідників.
RU_FUNCTION_WORDS: frozenset[str] = frozenset(  # ru-data
    {
        "и", "или", "но", "что", "как", "из", "от", "между",
        "согласно", "поскольку", "также", "является", "который",
        "которая", "которое", "которые", "которых", "его", "ее", "её", "их",
    }
)

# Лише форми з відмінним нормативним українським написанням. Спільні форми
# на кшталт «право» сюди не входять.
RU_SPELLING_FORMS: frozenset[str] = frozenset(  # ru-data
    {
        "теория", "теории", "теорию", "теорией", "теорий",
        "история", "истории", "историю", "историей",
        "организация", "организации", "организацию", "организацией",
        "информация", "информации", "информацию", "информацией",
        "классификация", "классификации", "классификацию",
        "методология", "методологии", "методологию",
        "конституция", "конституции", "конституцию",
        "концепция", "концепции", "концепцию",
        "функция", "функции", "функцию",
        "категория", "категории", "категорию",
        "юриспруденция", "юриспруденции",
    }
)

_CYRILLIC_WORD_RE = re.compile(r"[а-яёіїєґ]+(?:['’-][а-яёіїєґ]+)*", re.UNICODE)


@dataclass(frozen=True)
class LanguageDetection:
    """Детермінований результат класифікації одного текстового фрагмента."""

    language: Language
    evidence: str
    cyrillic_word_count: int
    ru_signals: tuple[str, ...]
    uk_signals: tuple[str, ...]


@dataclass(frozen=True)
class RussianWordEvidence:
    """Російське змістове слово з власним позитивним сигналом і координатами."""

    word: str
    start: int
    end: int
    signal: str


@dataclass(frozen=True)
class BibliographyLanguageStats:
    """Абсолютні числа мов, охоплення та умови показу відсотка RU."""

    total: int
    ru: int
    uk: int
    mixed: int
    unknown: int
    expected_count: int | None
    coverage_ratio: float | None
    sequentially_numbered: bool
    ru_ratio: float | None
    show_ru_percentage: bool
    reasons: tuple[str, ...]
    zero_ru_is_evidence: bool = False


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _word_matches(text: str) -> tuple[re.Match[str], ...]:
    return tuple(_CYRILLIC_WORD_RE.finditer(text))


def _exclusive_word_signals(
    matches: tuple[re.Match[str], ...], letters: frozenset[str], prefix: str
) -> tuple[str, ...]:
    return tuple(
        f"{prefix}:{match.group(0)}"
        for match in matches
        if any(letter in letters for letter in match.group(0))
    )


def detect_language(text: str) -> LanguageDetection:
    """Класифікує фрагмент за п'ятьма правилами PLAN_SEARCH.md, §9."""
    normalized = _normalized(text)
    matches = _word_matches(normalized)
    words = tuple(match.group(0) for match in matches)
    ru_exclusive = _exclusive_word_signals(matches, _RU_EXCLUSIVE, "exclusive")
    uk_exclusive = _exclusive_word_signals(matches, _UK_EXCLUSIVE, "exclusive")

    if ru_exclusive and uk_exclusive:
        language = Language.MIXED
        ru_signals = ru_exclusive
        uk_signals = uk_exclusive
    elif ru_exclusive:
        language = Language.RU
        ru_signals = ru_exclusive
        uk_signals = ()
    elif uk_exclusive:
        language = Language.UK
        ru_signals = ()
        uk_signals = uk_exclusive
    else:
        function_signals = tuple(
            f"function:{word}" for word in words if word in RU_FUNCTION_WORDS
        )
        spelling_signals = tuple(
            f"spelling:{word}" for word in words if word in RU_SPELLING_FORMS
        )
        ru_signals = function_signals + spelling_signals
        uk_signals = ()
        language = (
            Language.RU
            if len(words) >= MIN_CYRILLIC_WORDS and ru_signals
            else Language.UNKNOWN
        )

    if language == Language.UNKNOWN:
        reason = "insufficient_cyrillic" if len(words) < MIN_CYRILLIC_WORDS else "no_positive_signal"
        evidence = f"unknown:{reason};words={len(words)}"
    else:
        evidence = f"{language.value}:" + ",".join((*ru_signals, *uk_signals))
    return LanguageDetection(language, evidence, len(words), ru_signals, uk_signals)


def classify_language(text: str) -> Language:
    """Повертає лише категорію мови з єдиного детектора."""
    return detect_language(text).language


def annotate_bibliography(
    entries: tuple[BibliographyEntry, ...],
) -> tuple[BibliographyEntry, ...]:
    """Заповнює мову записів, не змінюючи їхні ID, текст або координати."""
    annotated: list[BibliographyEntry] = []
    for entry in entries:
        detection = detect_language(entry.raw_text)
        annotated.append(
            replace(entry, language=detection.language, language_evidence=detection.evidence)
        )
    return tuple(annotated)


def _prefix_normalized_lengths(text: str) -> tuple[int, ...]:
    return tuple(len(_normalized(text[:index])) for index in range(len(text) + 1))


def _raw_span_for_normalized(
    prefix_lengths: tuple[int, ...], start: int, end: int
) -> tuple[int, int]:
    raw_start = max(index for index, length in enumerate(prefix_lengths) if length <= start)
    raw_end = max(index for index, length in enumerate(prefix_lengths) if length <= end)
    return raw_start, max(raw_start + 1, raw_end)


def reliable_ru_content_words(text: str) -> tuple[RussianWordEvidence, ...]:
    """
    Повертає K2 лише слова з власним RU-сигналом; спільні слова відсіює.

    Службове слово може класифікувати весь запис як RU, але саме не є
    змістовним і не надає сусіднім словам російського походження.
    """
    normalized = _normalized(text)
    if detect_language(text).language != Language.RU:
        return ()

    prefix_lengths = _prefix_normalized_lengths(text)
    result: list[RussianWordEvidence] = []
    for match in _word_matches(normalized):
        word = match.group(0)
        if word in RU_FUNCTION_WORDS:
            continue
        if any(letter in _RU_EXCLUSIVE for letter in word):
            signal = "exclusive"
        elif word in RU_SPELLING_FORMS:
            signal = "spelling"
        else:
            continue
        raw_start, raw_end = _raw_span_for_normalized(prefix_lengths, match.start(), match.end())
        result.append(RussianWordEvidence(word, raw_start, raw_end, signal))
    return tuple(result)


def _numbering_metrics(
    entries: tuple[BibliographyEntry, ...],
) -> tuple[int | None, float | None, bool]:
    ordinals = [entry.ordinal for entry in entries]
    if not ordinals or any(value is None or value <= 0 for value in ordinals):
        return None, None, False
    numbers = [int(value) for value in ordinals if value is not None]
    unique = set(numbers)
    if len(unique) != len(numbers) or 1 not in unique:
        return None, None, False

    candidates = [
        number
        for number in sorted(unique)
        if (number - sum(1 for value in unique if 1 <= value <= number)) / number
        <= MAX_NUMBERING_MISSING_RATIO
    ]
    if not candidates:
        return None, None, False
    expected = max(candidates)
    within_series = sum(1 for value in numbers if value <= expected)
    coverage = within_series / expected
    sequential = all(value <= expected for value in numbers)
    return expected, coverage, sequential


def bibliography_language_stats(
    entries: tuple[BibliographyEntry, ...],
    boundary_confidence: Confidence,
) -> BibliographyLanguageStats:
    """Рахує мови й застосовує всі чотири умови показу відсотка RU."""
    detections = tuple(detect_language(entry.raw_text) for entry in entries)
    counts = {language: 0 for language in Language}
    for detection in detections:
        counts[detection.language] += 1

    total = len(entries)
    expected, coverage, sequential = _numbering_metrics(entries)
    uncertain_ratio = (
        (counts[Language.MIXED] + counts[Language.UNKNOWN]) / total if total else None
    )
    reasons: list[str] = []
    if total == 0:
        reasons.append("no_entries")
    if not sequential:
        reasons.append("not_sequentially_numbered")
    if coverage is None or coverage < MIN_BIBLIOGRAPHY_COVERAGE:
        reasons.append("coverage_below_90_percent")
    if uncertain_ratio is None or uncertain_ratio > MAX_UNCERTAIN_RATIO:
        reasons.append("uncertain_above_10_percent")
    if boundary_confidence not in (Confidence.HIGH, Confidence.MEDIUM):
        reasons.append("bibliography_boundary_low_confidence")

    return BibliographyLanguageStats(
        total=total,
        ru=counts[Language.RU],
        uk=counts[Language.UK],
        mixed=counts[Language.MIXED],
        unknown=counts[Language.UNKNOWN],
        expected_count=expected,
        coverage_ratio=coverage,
        sequentially_numbered=sequential,
        ru_ratio=counts[Language.RU] / total if total else None,
        show_ru_percentage=not reasons,
        reasons=tuple(reasons),
    )
