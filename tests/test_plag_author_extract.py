"""Тести розпізнавання автора й року з титулу звіту Plag.

`PLAN_PLAG_FILTER_V2.md`, §8.2, §9.2 (етап 1). Дані вигадані — прізвище
«Петренко», ім'я «Олена», по батькові «Андріївна», крім corpus-тестів, які
перевіряють лише властивості результату, без ПІБ.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plag_filter.pdf import parse_report
from plag_filter.rules import derive_initials, extract_author, extract_title_year, find_author

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
REPORT_2020 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-34-45.pdf"
REPORT_2002 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"


# ---------------------------------------------------------------------------
# derive_initials — §8.2
# ---------------------------------------------------------------------------


def test_derive_initials_two_parts() -> None:
    assert derive_initials("Олена", "Андріївна") == "О.А."


def test_derive_initials_given_name_only() -> None:
    assert derive_initials("Олена", "") == "О."


def test_derive_initials_empty() -> None:
    assert derive_initials("", "") == ""


# ---------------------------------------------------------------------------
# extract_author — §9.2 етап 1, вигадані дані
# ---------------------------------------------------------------------------


def test_extract_author_marker_split_title_confirmed() -> None:
    text = (
        "Кваліфікаційна наукова праця на правах рукопису ПЕТРЕНКО  835  "
        "ОЛЕНА АНДРІЇВНА  374  УДК 342.9 … __________О.  332  А. Петренко"
    )

    guess = extract_author(text)

    assert guess is not None
    assert guess.surname == "Петренко"
    assert guess.given_name == "Олена"
    assert guess.patronymic == "Андріївна"
    assert guess.initials == "О.А."
    assert guess.confidence == "confirmed"


def test_extract_author_glued_spaced_caps_single() -> None:
    text = "На правах рукописуП Е Т Р Е Н К О Олена АндріївнаУДК 343"

    guess = extract_author(text)

    assert guess is not None
    assert guess.surname == "Петренко"
    assert guess.given_name == "Олена"
    assert guess.patronymic == "Андріївна"
    assert guess.initials == "О.А."
    assert guess.confidence == "single"


def test_extract_author_male_patronymic_suffix() -> None:
    text = "Кваліфікаційна наукова праця на правах рукопису ПЕТРЕНКО ОЛЕГ ІВАНОВИЧ УДК 342.9"

    guess = extract_author(text)

    assert guess is not None
    assert guess.surname == "Петренко"
    assert guess.given_name == "Олег"
    assert guess.patronymic == "Іванович"
    assert guess.initials == "О.І."


def test_extract_author_no_anchor_returns_none() -> None:
    text = "Кваліфікаційна наукова праця ПЕТРЕНКО ОЛЕНА АНДРІЇВНА УДК 342.9"

    assert extract_author(text) is None


def test_extract_author_third_word_not_patronymic_returns_none() -> None:
    text = "На правах рукопису Петренко Олена Київ"

    assert extract_author(text) is None


# ---------------------------------------------------------------------------
# extract_author, extract_title_year — corpus, лише властивості результату
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_2020():
    return parse_report(REPORT_2020.read_bytes())


@pytest.fixture(scope="module")
def report_2002():
    return parse_report(REPORT_2002.read_bytes())


@pytest.mark.corpus
def test_extract_author_corpus_2020_confirmed(report_2020) -> None:
    guess = extract_author(report_2020.title_text)

    assert guess is not None
    assert guess.patronymic.casefold().endswith(
        ("ович", "евич", "йович", "івна", "ївна", "овна", "евна", "ич", "ична")
    )
    assert guess.initials == derive_initials(guess.given_name, guess.patronymic)
    assert find_author([report_2020.title_text], guess.surname, guess.initials) is not None
    assert guess.confidence == "confirmed"


@pytest.mark.corpus
def test_extract_author_corpus_2002_single(report_2002) -> None:
    guess = extract_author(report_2002.title_text)

    assert guess is not None
    assert guess.patronymic.casefold().endswith(
        ("ович", "евич", "йович", "івна", "ївна", "овна", "евна", "ич", "ична")
    )
    assert guess.initials == derive_initials(guess.given_name, guess.patronymic)
    # confidence == "single" означає, що другого згадування автора в тексті
    # титулу не знайдено (саме так рахується confidence — §8.2, крок 7).
    assert find_author([report_2002.title_text], guess.surname, guess.initials) is None
    assert guess.confidence == "single"


@pytest.mark.corpus
def test_extract_title_year_corpus(report_2020, report_2002) -> None:
    assert extract_title_year(report_2020.title_text) == 2020
    assert extract_title_year(report_2002.title_text) == 2002
