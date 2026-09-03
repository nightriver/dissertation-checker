"""Тести метаданих титульної частини для ручного пошуку."""

from types import SimpleNamespace

from search.metadata import extract_search_metadata
from search.types import SectionKind


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
