"""Модульні тести бібліографії та зв'язку згадок (PLAN_SEARCH.md, §12.5–12.6)."""

from __future__ import annotations

import hashlib

import fitz

from parser.searchdoc import parse_search_document
from search.bibliography import (
    REJECTION_REASONS,
    build_bibliography,
    build_bibliography_with_diagnostics,
    build_citations,
    build_citations_with_diagnostics,
    citation_id_for,
    donor_ids_for_mention,
    entry_id_for,
    normalized_entry_text,
)


_PAGE_RECT = fitz.Rect(72, 72, 523, 770)
_BIBLIOGRAPHY = [
    "1. Іванов І. І. Перша наукова праця. Київ, 2001. 90 с.",
    "2. Петров П. П. Друга наукова праця. Львів, 2010. 110 с.",
    "7. Захарченко З. З. Сьома наукова праця. Суми, 2007. 70 с.",
]


def _pdf_bytes(body: str | None, entries: list[str]) -> bytes:
    document = fitz.open()
    if body is not None:
        page = document.new_page(width=595, height=842)
        page.insert_htmlbox(_PAGE_RECT, f"<p>РОЗДІЛ 1</p><p>{body}</p>")
    page = document.new_page(width=595, height=842)
    bibliography = "<p>СПИСОК ЛІТЕРАТУРИ</p>" + "".join(
        f"<p>{entry}</p>" for entry in entries
    )
    page.insert_htmlbox(_PAGE_RECT, bibliography)
    result = document.tobytes()
    document.close()
    return result


def _document(body: str | None = None, entries: list[str] | None = None):
    return parse_search_document(_pdf_bytes(body, entries or _BIBLIOGRAPHY))


def test_identifier_helpers_follow_the_documented_hash_formulas() -> None:
    entry_key = "abc|none|Нормалізований текст"
    citation_key = "abc|block-1|17|numeric"

    assert entry_id_for("abc", None, "Нормалізований текст") == hashlib.sha256(
        entry_key.encode("utf-8")
    ).hexdigest()[:16]
    assert citation_id_for("abc", "block-1", 17, "numeric") == hashlib.sha256(
        citation_key.encode("utf-8")
    ).hexdigest()[:16]


def test_normalized_entry_text_collapses_line_wrapping_without_changing_case() -> None:
    assert normalized_entry_text("Назва\n  Наукової\tПраці") == "Назва Наукової Праці"


def test_empty_bibliography_exposes_all_zero_diagnostic_counters() -> None:
    raw = fitz.open()
    page = raw.new_page(width=595, height=842)
    page.insert_htmlbox(_PAGE_RECT, "<p>ВСТУП</p><p>Текст дослідження.</p>")
    data = raw.tobytes()
    raw.close()
    document = parse_search_document(data)

    entries, diagnostics = build_bibliography_with_diagnostics(document)

    assert entries == ()
    assert tuple(diagnostics.as_dict()) == REJECTION_REASONS
    assert all(value == 0 for value in diagnostics.as_dict().values())


def test_bibliography_builder_keeps_source_coordinates_reconstructable() -> None:
    document = _document()

    entries = build_bibliography(document)
    blocks = {block.block_id: block for block in document.blocks}

    assert [entry.ordinal for entry in entries] == [1, 2, 7]
    for entry in entries:
        reconstructed = "".join(
            blocks[part.block_id].raw_text[part.raw_start : part.raw_end]
            for part in entry.source.parts
        )
        assert reconstructed == entry.raw_text


def test_unresolved_and_oversized_numbers_are_reported_not_silenced() -> None:
    document = _document("Твердження підтверджують джерела [99] і [1000].")
    entries = build_bibliography(document)

    citations, diagnostics = build_citations_with_diagnostics(document, entries)

    assert citations == ()
    assert diagnostics.count("unresolved_source_number") >= 1
    assert diagnostics.count("source_number_too_large") == 1


def test_own_reference_sentence_blocks_the_previous_donor() -> None:
    control = _document(
        "Проблема широко висвітлена в літературі. Джерело [2] це підтверджує."
    )
    blocked = _document(
        "Проблема широко висвітлена в літературі. "
        "Інше джерело це підтверджує [7]. Джерело [2] це підтверджує."
    )

    def linked_count(document) -> int:
        entries = build_bibliography(document)
        entry2 = next(entry for entry in entries if entry.ordinal == 2)
        mention = next(
            item for item in build_citations(document, entries)
            if entry2.entry_id in item.entry_ids
        )
        return len(donor_ids_for_mention(document, mention))

    assert linked_count(control) == 2
    assert linked_count(blocked) == 1


def test_citation_diagnostics_always_keep_the_complete_reason_schema() -> None:
    document = _document("Джерело [2] підтверджує висновок.")
    entries = build_bibliography(document)

    _, diagnostics = build_citations_with_diagnostics(document, entries)

    assert tuple(diagnostics.as_dict()) == REJECTION_REASONS
