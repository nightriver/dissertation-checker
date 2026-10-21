"""Незалежні очікування та синтетичні PDF — PLAN_REPORT_FIRST_PAGE.md, крок B."""

from dataclasses import asdict, replace
from datetime import date
from html import escape
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from report_first_page import extractor
from report_first_page.extractor import (
    MAX_PDF_BYTES, ReportInputError, extract_report_metadata, normalize_person_name,
    validate_report_upload,
)
from report_first_page.presentation import build_report_page

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/report_first_page_expectations.json").read_text(encoding="utf-8"))


def pdf_with_text(texts):
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page()
            page.insert_htmlbox(pymupdf.Rect(36, 36, 560, 800),
                                '<div style="font-size:10pt">' + escape(text).replace("\n", "<br>") + "</div>")
        return document.tobytes()


def minimal_pdf(service="", degree="наукового ступеня кандидата юридичних наук"):
    return pdf_with_text([
        "МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ\nКОВАЛЬ ОЛЕНА ІВАНІВНА\nУДК 349.2\n"
        "ПРАВОВІ ЗАСАДИ\nСпеціальність 12.00.05\nАВТОРЕФЕРАТ\n"
        f"дисертації на здобуття {degree}\nКиїв – 2021",
        service,
    ])


@pytest.mark.corpus
@pytest.mark.parametrize("filename", CORPUS)
def test_all_six_pdfs_match_hand_checked_records(filename):
    metadata = extract_report_metadata((ROOT / "examples/avtoreferats" / filename).read_bytes())
    actual = asdict(metadata)
    actual["opponents"] = list(actual["opponents"])
    assert actual.pop("warnings") == ()
    assert actual == CORPUS[filename]


@pytest.mark.corpus
def test_corrupted_structural_raw_service_text_cannot_change_service_values(monkeypatch):
    source = (ROOT / "examples/avtoreferats/aref-Желтобрюх.pdf").read_bytes()
    expected = extract_report_metadata(source)
    original_parse = extractor.parse_search_document
    def corrupt(data):
        document = original_parse(data)
        return replace(document, blocks=tuple(
            replace(block, raw_text=block.raw_text.replace("університет імені", "університетімені")
                    .replace("липня 2016 року", "липня2016року"))
            for block in document.blocks
        ))
    monkeypatch.setattr(extractor, "parse_search_document", corrupt)
    assert extract_report_metadata(source) == expected


@pytest.mark.corpus
def test_metadata_year_is_ignored_in_both_report_dates(monkeypatch):
    source = (ROOT / "examples/avtoreferats/aref_MatsyukVYa.pdf").read_bytes()
    original = extractor.extract_search_metadata
    baseline = extract_report_metadata(source)
    monkeypatch.setattr(extractor, "extract_search_metadata", lambda doc: replace(original(doc), year=2999))
    result = extract_report_metadata(source)
    assert result == baseline
    page = build_report_page(result, generated_on=date(2026, 10, 21))
    assert page.paragraphs[-4].text.startswith("Захист відбувся «27» грудня 2017 року")
    assert page.paragraphs[-1].text == "Київ, жовтень 2026"


def test_people_do_not_depend_on_empty_lines_semicolons_or_role_line_breaks():
    result = extract_report_metadata(minimal_pdf(
        "Науковий консультант: доктор юридичних наук, професор,\n"
        "академік НАПрН України\nІНШИН Микола Іванович,\nУніверситет, завідувач кафедри.\n"
        "Офіційні опоненти: доктор юридичних наук, доцент\nКОВАЛЬ-ПЕТРЕНКО Олена Іванівна,\n"
        "Науково-\nдослідний інститут, професор\n"
        "кандидат юридичних наук\nП'ЯТНИЦЬКИЙ Іван Андрійович,\nІнститут, дослідник\n"
        "доктор філософії\nУстанова без ПІБ\nЗахист відбудеться «13» травня 2021 р. "
        "о 13.00 годині на засіданні спеціалізованої вченої ради К 26.504.01 у Інституті за адресою: Київ."
    ))
    assert result.supervisor_label == "Науковий консультант"
    assert result.supervisor.before_name == "доктор юридичних наук, професор, академік НАПрН України"
    assert result.supervisor.full_name == "Іншин Микола Іванович"
    assert len(result.opponents) == 3
    assert result.opponents[0].full_name == "Коваль-Петренко Олена Іванівна"
    assert result.opponents[0].after_name == "Науково-дослідний інститут, професор"
    assert result.opponents[1].full_name == "П'ятницький Іван Андрійович"
    assert result.opponents[2].full_name is None
    assert "Установа без ПІБ" in result.opponents[2].before_name
    assert any("запис 3" in w for w in result.warnings)
    assert result.defense_day == "13" and result.defense_year == "2021"


@pytest.mark.parametrize("name,expected", [
    ("КОВАЛЬ-ПЕТРЕНКО ОЛЕНА ІВАНІВНА", "Коваль-Петренко Олена Іванівна"),
    ("П’ЯТНИЦЬКИЙ Іван Андрійович", "П’ятницький Іван Андрійович"),
    ("Д’Артаньян Іван Миколайович", "Д’Артаньян Іван Миколайович"),
    ("І КОВАЛЬ Олена", "І Коваль Олена"),
    ("КОВАЛЬʼІВАНОВ ОЛЕНА ІВАНІВНА", "Ковальʼіванов Олена Іванівна"),
])
def test_name_case_changes_only_uppercase_parts(name, expected):
    assert normalize_person_name(name) == expected


@pytest.mark.parametrize("degree", ["наукового ступеня кандидата юридичних наук",
                                   "ступеня доктора філософії", "наукового\nступеня доктора юридичних\nнаук"])
def test_degree_preserves_source_scientific_word(degree):
    result = extract_report_metadata(minimal_pdf(degree=degree))
    assert result.degree_phrase == " ".join(degree.split())


def test_missing_degree_is_not_recovered_from_supervisor_qualifications():
    result = extract_report_metadata(minimal_pdf(
        "Науковий керівник: доктор юридичних наук\nКоваль Іван Миколайович,\nУніверситет",
        degree="ступеня кандидата",
    ))
    assert result.degree_phrase is None


@pytest.mark.parametrize("time", ["о 10 годині", "о «12» годині", "о 13.00 годині"])
@pytest.mark.parametrize("council,tail", [("К 26.504.01", "у Інституті"), ("Д 26.001.46", "Університету"),
                                        ("", "у Інституті"), ("К 26.504.01", ""), ("", "")])
def test_defense_date_time_council_and_institution_are_independent(time, council, tail):
    result = extract_report_metadata(minimal_pdf(
        f"Захист відбудеться 13 травня 2021 р. {time} на засіданні спеціалізованої\n"
        f"вченої ради {council.replace(' ', chr(10))}\n{tail} за адресою: м. Київ.\n"
        "Автореферат розіслано «12» квітня 2021 року."
    ))
    assert (result.defense_day, result.defense_month, result.defense_year) == ("13", "травня", "2021")
    assert result.council_code == (council or None)
    assert result.institution_tail == (tail or None)
    assert "годині" not in (result.institution_tail or "")


@pytest.mark.parametrize("date_text,expected", [
    ("«31» лютого 2021 року", ("31", "лютого", "2021")),
    ("травня 2021 року", (None, "травня", "2021")),
    ("«13» 2021 року", ("13", None, "2021")),
    ("«13» травня", ("13", "травня", None)),
    ("", (None, None, None)),
])
def test_partial_or_invalid_defense_date_is_visible_for_editing(date_text, expected):
    result = extract_report_metadata(minimal_pdf(
        f"Захист відбудеться {date_text} на засіданні спеціалізованої вченої ради К 26.504.01 у Інституті."
    ))
    assert (result.defense_day, result.defense_month, result.defense_year) == expected
    assert any("дат" in w.casefold() for w in result.warnings)


def test_only_first_four_pages_are_read_with_plain_text(monkeypatch):
    data = pdf_with_text(["Текст аркуша"] * 4 + ["Офіційні опоненти:\nдоктор юридичних наук\nКоваль Іван Іванович,"])
    original = pymupdf.Page.get_text
    calls = []
    def tracked(page, option="text", *args, **kwargs):
        if option == "text":
            calls.append(page.number)
        return original(page, option, *args, **kwargs)
    monkeypatch.setattr(pymupdf.Page, "get_text", tracked)
    result = extract_report_metadata(data)
    assert calls == [0, 1, 2, 3]
    assert result.opponents == ()


@pytest.mark.parametrize("data", [b"", b"not a pdf"])
def test_invalid_pdf_is_an_explained_input_error(data):
    with pytest.raises(ReportInputError):
        extract_report_metadata(data)


def test_scanned_and_encrypted_pdf_are_rejected():
    with pymupdf.open() as pdf:
        pdf.new_page()
        scanned = pdf.tobytes()
        protected = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(ReportInputError, match="текстового шару"):
        extract_report_metadata(scanned)
    with pytest.raises(ReportInputError, match="паролем"):
        extract_report_metadata(protected)


def test_upload_type_and_size_limit_are_checked_before_parsing(monkeypatch):
    assert validate_report_upload("source.PDF", MAX_PDF_BYTES) is None
    assert "30 МБ" in validate_report_upload("source.pdf", MAX_PDF_BYTES + 1)
    assert "PDF" in validate_report_upload("source.docx", 100)
    monkeypatch.setattr(extractor, "MAX_PDF_BYTES", 10)
    with pytest.raises(ReportInputError, match="30 МБ"):
        extract_report_metadata(b"a" * 11)


def test_title_case_requires_full_match_in_ukrainian_annotation():
    def document(text):
        return SimpleNamespace(blocks=(SimpleNamespace(physical_page=1, raw_text=text),))
    title = "ПРАВОВІ ЗАСАДИ В УКРАЇНІ"
    assert extractor._annotation_title(document("АНОТАЦІЯ\nКоваль О.І. Правові засади в Україні."), title,
                                       "Коваль Олена Іванівна") == "Правові засади в Україні"
    assert extractor._annotation_title(document("АНОТАЦІЯ\nКоваль О.І. Правові засади."), title, None) == title
    assert extractor._annotation_title(document("АННОТАЦИЯ\nКоваль О.І. Правові засади в Україні."), title, None) == title
    assert extractor._annotation_title(document("АНОТАЦІЯ"), None, None) is None


def test_missing_name_does_not_lose_known_qualifications_or_merge_next_person():
    result = extract_report_metadata(minimal_pdf(
        "Офіційні опоненти: доктор юридичних наук\nЗаслужений юрист України,\nІнститут А\n"
        "кандидат юридичних наук\nКОВАЛЬ Іван Миколайович,\nІнститут Б\nЗахист відбудеться"
    ))
    assert len(result.opponents) == 2
    assert result.opponents[0].full_name is None
    assert "Інститут А" in result.opponents[0].before_name
    assert result.opponents[1].full_name == "Коваль Іван Миколайович"


def test_repeated_role_is_reported_instead_of_overwriting_first_record():
    result = extract_report_metadata(minimal_pdf(
        "Науковий керівник: доктор юридичних наук\nКоваль Іван Миколайович,\nІнститут А\n"
        "Науковий керівник: доктор юридичних наук\nПетренко Іван Миколайович,\nІнститут Б"
    ))
    assert result.supervisor.full_name == "Коваль Іван Миколайович"
    assert any("Повторний" in w for w in result.warnings)
