from parser.year_extractor import extract_year, extract_years
from parser.dstu_validator import check_dstu, DstuStatus
from parser.extractor import extract_dissertation_year


# --- year_extractor ---

def test_year_dstu_standard():
    assert extract_year(
        "Петренко В.А. Назва / В.А. Петренко. – Київ : Либідь, 2018. – 240 с."
    ) == 2018


def test_year_in_parentheses():
    assert extract_year("Smith J. (2021). Title. Publisher.") == 2021


def test_year_end_of_line():
    assert extract_year("Publisher, 2019") == 2019


def test_year_url_date_ignored():
    entry = "Стаття. – Київ, 2015. URL : https://example.com (дата звернення: 12.05.2023)."
    assert extract_year(entry) == 2015


def test_year_no_year():
    assert extract_year("Міжнародні стандарти ISO/IEC 27001") is None


def test_extract_years_batch():
    bib = {1: "Київ, 2020.", 2: "(2015). Title.", 3: "без року"}
    result = extract_years(bib)
    assert result[1] == 2020
    assert result[2] == 2015
    assert result[3] is None


# --- dstu_validator ---

def test_dstu_valid():
    assert check_dstu(
        "Петренко В.А. Назва / В.А. Петренко. – Київ : Либідь, 2018. – 240 с."
    ) == DstuStatus.DSTU


def test_dstu_apa_format():
    assert check_dstu(
        "Smith J. (2021). The title of the book. Publisher."
    ) == DstuStatus.OTHER


def test_dstu_vancouver():
    assert check_dstu(
        "Smith J. Title. Journal. 2021;10(3):pp. 12-25."
    ) == DstuStatus.OTHER


def test_dstu_partial():
    assert check_dstu(
        "Київ : Наукова думка, 2010."
    ) == DstuStatus.PARTIAL


def test_dstu_url_resource():
    assert check_dstu(
        "Назва ресурсу. URL : https://example.com (дата звернення: 01.01.2024)."
    ) == DstuStatus.DSTU


# --- extract_dissertation_year ---

def test_dissertation_year_from_lines():
    lines = [
        {"line": "НАЦІОНАЛЬНИЙ УНІВЕРСИТЕТ", "page": 1},
        {"line": "Кваліфікаційна наукова праця", "page": 1},
        {"line": "Київ – 2023", "page": 2},
    ]
    assert extract_dissertation_year(lines) == 2023


def test_dissertation_year_empty():
    assert extract_dissertation_year([]) is None
