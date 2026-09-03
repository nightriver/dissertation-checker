"""Тести метаданих титульної частини для ручного пошуку."""

from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from search.metadata import extract_search_metadata
from search.types import SectionKind


# Авторів і назви перевірив користувач; 2013 рік додатково прочитано з титулу Коцюби.
VERIFIED_METADATA = json.loads(
    (Path(__file__).parent / "fixtures" / "search_metadata_expectations.json").read_text(encoding="utf-8")
)


@pytest.mark.corpus
@pytest.mark.parametrize("expected", VERIFIED_METADATA, ids=[str(i + 1) for i in range(len(VERIFIED_METADATA))])
def test_verified_metadata_from_real_documents(canonical_corpus, expected):
    item = next(item for item in canonical_corpus if item.path.name == expected["file"])
    metadata = extract_search_metadata(item.document)
    assert metadata.author is not None, expected["file"]
    assert metadata.author.casefold() == expected["author"].casefold(), expected["file"]
    assert metadata.title == expected["title"], expected["file"]
    assert metadata.year == expected["year"], expected["file"]


def _title_document(*pages):
    return SimpleNamespace(
        sections=(SimpleNamespace(section_id="title", kind=SectionKind.TITLE),),
        blocks=tuple(
            SimpleNamespace(section_id="title", physical_page=index, raw_text=text)
            for index, text in enumerate(pages, 1)
        ),
    )


@pytest.mark.parametrize("ending", [
    "Спеціальність 12.00.01 – теорія та історія держави і права",
    "12.00.01 – теорія та історія держави і права",
    "081 – Право",
    "Подається на здобуття ступеня доктора філософії",
    "Д и с е р т а ц і я",
])
def test_complete_title_preserves_short_lines_and_compound_hyphen(ending):
    metadata = extract_search_metadata(_title_document(
        "ПЕТРЕНКО\nІВАН ІВАНОВИЧ\nУДК 343.228\nДИСЕРТАЦІЯ\n"
        "КРИМІНАЛЬНА ВІДПОВІДАЛЬНІСТЬ:\nПОРІВНЯЛЬНО-\nПРАВОВЕ ДОСЛІДЖЕННЯ\nВ УКРАЇНІ\n"
        f"{ending}\n"
        "Дисертація містить результати власних досліджень та посилання на всі використані джерела.\n"
        "Науковий керівник\nІВАНЕНКО МИКОЛА ПЕТРОВИЧ\nКиїв – 2020"
    ))
    assert metadata.author == "Петренко Іван Іванович"
    assert metadata.title == "КРИМІНАЛЬНА ВІДПОВІДАЛЬНІСТЬ: ПОРІВНЯЛЬНО-ПРАВОВЕ ДОСЛІДЖЕННЯ В УКРАЇНІ"
    assert metadata.year == 2020


def test_institution_is_not_author_when_udk_is_moved_to_page_end():
    metadata = extract_search_metadata(_title_document(
        "ЗАКЛАД  ВИЩОЇ  ОСВІТИ\nУНІВЕРСИТЕТ ПРАВА\n"
        "КОВАЛЬ МАРІЯ ПЕТРІВНА\nОРГАНІЗАЦІЙНІ ЗАСАДИ\nСУДОУСТРОЮ\nДисертація\n"
        "Науковий керівник\nІВАНЕНКО МИКОЛА ПЕТРОВИЧ\nЛьвів - 2020\n1\nУДК 347.94/.99"
    ))
    assert metadata.author == "Коваль Марія Петрівна"
    assert metadata.title == "ОРГАНІЗАЦІЙНІ ЗАСАДИ СУДОУСТРОЮ"


def test_title_stays_on_same_page_and_keeps_institution_words_in_topic():
    metadata = extract_search_metadata(_title_document(
        "ПЕТРЕНКО ІВАН ІВАНОВИЧ\nУДК 004.9\nРОЗВИТОК ОСВІТИ В УНІВЕРСИТЕТІ",
        "АНОТАЦІЯ ТА ІНШИЙ ТЕКСТ, ЯКИЙ НЕ Є ЧАСТИНОЮ НАЗВИ\nКиїв – 2020",
    ))
    assert metadata.title == "РОЗВИТОК ОСВІТИ В УНІВЕРСИТЕТІ"


def test_unknown_author_uses_udk_for_title_and_does_not_use_supervisor():
    metadata = extract_search_metadata(_title_document(
        "УДК 004.9\nРОЗВИТОК ЦИФРОВИХ ТЕХНОЛОГІЙ В ОСВІТІ\n"
        "Науковий керівник\nІВАНЕНКО МИКОЛА ПЕТРОВИЧ\nКиїв – 2020"
    ))
    assert metadata.author is None
    assert metadata.title == "РОЗВИТОК ЦИФРОВИХ ТЕХНОЛОГІЙ В ОСВІТІ"


def test_title_can_be_on_second_page_after_empty_cover():
    metadata = extract_search_metadata(_title_document(
        "МІНІСТЕРСТВО ОСВІТИ І НАУКИ УКРАЇНИ",
        "ПЕТРЕНКО ІВАН ІВАНОВИЧ\nУДК 004.9\nЦИФРОВІ ТЕХНОЛОГІЇ В ОСВІТІ\nКиїв – 2020",
    ))
    assert metadata.author == "Петренко Іван Іванович"
    assert metadata.title == "ЦИФРОВІ ТЕХНОЛОГІЇ В ОСВІТІ"


def test_title_page_metadata_uses_author_title_and_bottom_year() -> None:
    document = SimpleNamespace(
        sections=(SimpleNamespace(section_id="title", kind=SectionKind.TITLE),),
        blocks=(SimpleNamespace(
            section_id="title",
            physical_page=1,
            raw_text=(
                "НАЦІОНАЛЬНИЙ УНІВЕРСИТЕТ\n"
                "ПЕТРЕНКО ІВАН ІВАНОВИЧ\n"
                "УДК 004.9\n"
                "ЦИФРОВІ ТЕХНОЛОГІЇ В ОСВІТНІЙ ПОЛІТИЦІ\n"
                "Київ – 2022"
            ),
        ),),
    )

    metadata = extract_search_metadata(document)

    assert metadata.author == "Петренко Іван Іванович"
    assert metadata.title == "ЦИФРОВІ ТЕХНОЛОГІЇ В ОСВІТНІЙ ПОЛІТИЦІ"
    assert metadata.year == 2022


def test_metadata_ignores_non_title_sections() -> None:
    document = SimpleNamespace(
        sections=(SimpleNamespace(section_id="chapter", kind=SectionKind.CHAPTER),),
        blocks=(SimpleNamespace(
            section_id="chapter", physical_page=3,
            raw_text="ПЕТРЕНКО ІВАН ІВАНОВИЧ\nКиїв – 2022",
        ),),
    )

    assert extract_search_metadata(document).author is None
