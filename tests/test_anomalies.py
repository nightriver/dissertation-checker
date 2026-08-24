"""
Анахронізми: джерело не може бути новішим за роботу, яка на нього посилається.

Чисті функції над словниками — PyMuPDF і python-docx тут не потрібні.
"""

from parser.anomalies import find_anachronisms
from parser.types import Severity
from parser.year_extractor import extract_year_with_confidence


# ---------------------------------------------------------------------------
# extract_year_with_confidence
# ---------------------------------------------------------------------------

def test_confidence_strong_for_dstu_year_field():
    year, conf = extract_year_with_confidence("Іванов І. І. Право. Київ, 2019. 320 с.")
    assert (year, conf) == (2019, "strong")


def test_confidence_strong_for_year_in_parentheses():
    assert extract_year_with_confidence("Smith J. Law (2015). P. 4.") == (2015, "strong")


def test_confidence_weak_for_year_inside_title():
    """Рік із назви закону ловиться лише загальним патерном."""
    year, conf = extract_year_with_confidence("Закон України 2020 року про освіту")
    assert (year, conf) == (2020, "weak")


def test_confidence_none_when_no_year():
    assert extract_year_with_confidence("Запис без жодного року") == (None, "none")


# ---------------------------------------------------------------------------
# find_anachronisms — правила
# ---------------------------------------------------------------------------

def test_two_years_newer_with_strong_pattern_is_proof():
    result = find_anachronisms({1: 2022}, {1: "strong"}, dissertation_year=2020)
    assert result[1].severity is Severity.PROOF
    assert result[1].delta == 2
    assert result[1].source_year == 2022


def test_same_gap_with_weak_pattern_is_only_suspect():
    result = find_anachronisms({1: 2022}, {1: "weak"}, dissertation_year=2020)
    assert result[1].severity is Severity.SUSPECT


def test_one_year_newer_with_strong_pattern_is_suspect_not_proof():
    """+1 рік — це регулярно рік подання проти року захисту, а не підробка."""
    result = find_anachronisms({1: 2021}, {1: "strong"}, dissertation_year=2020)
    assert result[1].severity is Severity.SUSPECT
    assert result[1].delta == 1


def test_future_year_is_proof_even_without_dissertation_year():
    result = find_anachronisms(
        {1: 2029}, {1: "strong"}, dissertation_year=None, current_year=2026
    )
    assert result[1].severity is Severity.PROOF
    assert result[1].delta == 0


def test_future_year_reports_delta_when_dissertation_year_known():
    result = find_anachronisms(
        {1: 2029}, {1: "weak"}, dissertation_year=2020, current_year=2026
    )
    assert result[1].severity is Severity.PROOF
    assert result[1].delta == 9


def test_no_dissertation_year_and_no_future_year_gives_nothing():
    result = find_anachronisms(
        {1: 2019, 2: 2020}, {1: "strong", 2: "weak"},
        dissertation_year=None, current_year=2026,
    )
    assert result == {}


def test_current_year_none_disables_only_the_future_rule():
    """Без current_year правило «рік ще не настав» мовчить, решта працює."""
    result = find_anachronisms(
        {1: 2029, 2: 2022}, {1: "strong", 2: "strong"},
        dissertation_year=2020, current_year=None,
    )
    # 2029 більше не «рік у майбутньому», але все ще +9 до дисертації
    assert result[1].severity is Severity.PROOF
    assert result[1].reason == "джерело новіше за рік на титульній сторінці"
    assert result[2].severity is Severity.PROOF


def test_same_year_as_dissertation_is_not_a_finding():
    """Робота може цитувати видання свого року."""
    assert find_anachronisms({1: 2020}, {1: "strong"}, dissertation_year=2020) == {}


def test_older_sources_are_not_findings():
    result = find_anachronisms(
        {1: 1998, 2: 2015}, {1: "strong", 2: "weak"}, dissertation_year=2020
    )
    assert result == {}


def test_entries_without_year_are_skipped():
    result = find_anachronisms(
        {1: None, 2: 2022, 3: None},
        {1: "none", 2: "strong", 3: "none"},
        dissertation_year=2020,
    )
    assert set(result) == {2}


def test_weak_pattern_plus_one_year_is_suspect():
    result = find_anachronisms({1: 2021}, {1: "weak"}, dissertation_year=2020)
    assert result[1].severity is Severity.SUSPECT
    assert result[1].reason == "рік видання визначено неточно — перевірте запис"


def test_missing_confidence_key_defaults_to_no_finding():
    """Немає запису в confidence → жодне з правил delta не спрацьовує."""
    assert find_anachronisms({1: 2022}, {}, dissertation_year=2020) == {}


def test_delta_is_computed_per_entry():
    result = find_anachronisms(
        {1: 2021, 2: 2023, 3: 2025},
        {1: "strong", 2: "strong", 3: "strong"},
        dissertation_year=2020,
    )
    assert [result[n].delta for n in (1, 2, 3)] == [1, 3, 5]


def test_empty_input_gives_empty_result():
    assert find_anachronisms({}, {}, dissertation_year=2020, current_year=2026) == {}


# ---------------------------------------------------------------------------
# extract_years_with_confidence — вхід для find_anachronisms
# ---------------------------------------------------------------------------

def test_extract_years_with_confidence_returns_two_aligned_dicts():
    from parser.year_extractor import extract_years_with_confidence

    years, confidence = extract_years_with_confidence({
        1: "Іванов І. І. Право. Київ, 2019. 320 с.",
        2: "Закон України 2020 року про освіту",
        3: "Запис без року",
    })
    assert years == {1: 2019, 2: 2020, 3: None}
    assert confidence == {1: "strong", 2: "weak", 3: "none"}


def test_extract_years_with_confidence_on_empty_bibliography():
    from parser.year_extractor import extract_years_with_confidence

    assert extract_years_with_confidence({}) == ({}, {})
