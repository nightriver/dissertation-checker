"""Перевірки безпечного підсвічування порівняльних таблиць DOCX."""

from __future__ import annotations

import io

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import pytest

from table_highlighter.matcher import align, left_statuses, right_statuses
from table_highlighter.processor import DocumentValidationError, inspect_tables, process_document
from table_highlighter.types import HighlightOptions
from table_highlighter.zones import annotate_left_cell, annotate_right_cell, paragraph_text


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """Створює реальне w:hyperlink, а не текст URL для перевірки збереження."""
    part = paragraph.part
    relation = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation)
    run = OxmlElement("w:r")
    value = OxmlElement("w:t")
    value.text = text
    run.append(value)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _table_docx(rows: list[tuple[list[str], list[str]]], *, link_in_right: bool = False) -> bytes:
    document = Document()
    table = document.add_table(rows=0, cols=2)
    for row_index, (left, right) in enumerate(rows):
        row = table.add_row()
        for cell, paragraphs in zip(row.cells, (left, right)):
            cell.text = paragraphs[0]
            for text in paragraphs[1:]:
                cell.add_paragraph(text)
        if link_in_right and row_index == 0:
            _add_hyperlink(row.cells[1].paragraphs[0], " активне посилання", "https://example.test/source")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _cell_texts(cell) -> list[str]:
    return [paragraph_text(paragraph) for paragraph in cell.paragraphs]


def _has_hyperlink(cell) -> bool:
    return bool(cell._tc.findall(".//" + qn("w:hyperlink")))


class TestZones:
    def test_right_comment_keeps_every_following_paragraph_plain(self):
        data = _table_docx([
            (["С. 1", "Текст", "", "Примітка"], ["Джерело", "С. 2", "Текст", "", "Посилання на:", "29. Джерело", "", "60. Інше джерело"]),
        ])
        document = Document(io.BytesIO(data))
        right = annotate_right_cell(document.tables[0].rows[0].cells[1])

        assert [item.zone for item in right.paragraphs] == ["plain", "plain", "text", "text", "plain", "plain", "plain", "plain"]
        assert [paragraph_text(item.paragraph) for item in right.paragraphs[-3:]] == ["29. Джерело", "", "60. Інше джерело"]

    def test_left_marks_text_only_before_first_empty_paragraph(self):
        data = _table_docx([(["С. 1", "Текст", "", "Коментар"], ["С. 2", "Текст"])])
        document = Document(io.BytesIO(data))
        left = annotate_left_cell(document.tables[0].rows[0].cells[0])
        assert [item.zone for item in left.paragraphs] == ["plain", "text", "plain", "plain"]


class TestMatcher:
    def test_threshold_100_disables_relaxed_short_word_match(self):
        data = _table_docx([(["С. 1", "суд"], ["С. 2", "суду"])])
        document = Document(io.BytesIO(data))
        left = annotate_left_cell(document.tables[0].rows[0].cells[0])
        right = annotate_right_cell(document.tables[0].rows[0].cells[1])
        result = align(left.text_paragraphs, right.text_paragraphs, 100, True)
        assert set(left_statuses(result)[1]) == {"diff"}
        assert set(right_statuses(result)[1]) == {"diff"}

    def test_normalized_hyphen_is_only_a_matching_key(self):
        data = _table_docx([(["С. 1", "Ін- тернет"], ["С. 2", "Інтернет"])])
        document = Document(io.BytesIO(data))
        left = annotate_left_cell(document.tables[0].rows[0].cells[0])
        right = annotate_right_cell(document.tables[0].rows[0].cells[1])
        result = align(left.text_paragraphs, right.text_paragraphs, 75, True)
        assert set(left_statuses(result)[1]) == {"match"}
        assert set(right_statuses(result)[1]) == {"match"}

    def test_punctuation_between_matches_does_not_inherit_highlight(self):
        data = _table_docx([(["С. 1", "слово — слово"], ["С. 2", "слово — слово"])])
        document = Document(io.BytesIO(data))
        left = annotate_left_cell(document.tables[0].rows[0].cells[0])
        right = annotate_right_cell(document.tables[0].rows[0].cells[1])
        result = align(left.text_paragraphs, right.text_paragraphs, 75, True)
        statuses = left_statuses(result)[1]
        assert statuses[6] is None
        assert statuses[0] == "match"
        assert statuses[-1] == "match"


class TestProcessor:
    def test_preserves_comment_tail_hyperlink_and_original_hyphen(self):
        data = _table_docx([
            (
                ["С. 1", "Ін- тернет суду", "", "Коментар"],
                ["Джерело", "С. 2", "Інтернет суд", "", "Посилання на:", "29. Джерело", "", "60. Інше джерело"],
            ),
        ], link_in_right=True)
        result = process_document(data, HighlightOptions())
        document = Document(io.BytesIO(result.document_bytes))
        left, right = document.tables[0].rows[0].cells

        assert result.stats.processed_rows == 1
        assert "Ін- тернет суду" in _cell_texts(left)
        assert _cell_texts(right)[-3:] == ["29. Джерело", "", "60. Інше джерело"]
        assert _has_hyperlink(right)
        colors = [str(run.font.highlight_color) for paragraph in left.paragraphs for run in paragraph.runs]
        assert "YELLOW (7)" in colors

    def test_preserves_hyperlink_inside_compared_text(self):
        data = _table_docx([(["С. 1", "Спільний текст"], ["С. 2", "Спільний текст"])])
        document = Document(io.BytesIO(data))
        right = document.tables[0].rows[0].cells[1]
        _add_hyperlink(right.paragraphs[1], " посилання", "https://example.test/inside")
        raw = io.BytesIO()
        document.save(raw)

        result = process_document(raw.getvalue(), HighlightOptions())
        output = Document(io.BytesIO(result.document_bytes))
        cell = output.tables[0].rows[0].cells[1]
        assert _has_hyperlink(cell)
        assert "посилання" in paragraph_text(cell.paragraphs[1])

    def test_alignment_adds_blank_paragraphs_before_left_marker(self):
        data = _table_docx([(["С. 1", "Однаковий текст"], ["Бібліографія", "С. 2", "Однаковий текст"])])
        result = process_document(data, HighlightOptions())
        document = Document(io.BytesIO(result.document_bytes))
        left = document.tables[0].rows[0].cells[0]
        assert result.stats.padding_paragraphs == 1
        assert _cell_texts(left)[:2] == ["", "С. 1"]

    def test_reprocessing_does_not_accumulate_alignment_padding(self):
        data = _table_docx([(["С. 1", "Текст"], ["Джерело", "С. 2", "Текст"])])
        first = process_document(data, HighlightOptions())
        second = process_document(first.document_bytes, HighlightOptions())
        document = Document(io.BytesIO(second.document_bytes))
        assert _cell_texts(document.tables[0].rows[0].cells[0])[:2] == ["", "С. 1"]
        assert second.stats.padding_paragraphs == 0

    def test_missing_marker_keeps_row_without_destroying_empty_paragraphs(self):
        data = _table_docx([(["Текст", "", "Інший текст"], ["С. 2", "Текст"])])
        result = process_document(data, HighlightOptions())
        document = Document(io.BytesIO(result.document_bytes))
        assert result.stats.skipped_rows == 1
        assert _cell_texts(document.tables[0].rows[0].cells[0]) == ["Текст", "", "Інший текст"]
        assert result.warnings[0].row_number == 1

    def test_inspect_tables_and_selected_table_leave_other_tables_unchanged(self):
        document = Document()
        first = document.add_table(rows=1, cols=2)
        first.cell(0, 0).text = "Не обробляти"
        first.cell(0, 1).text = "Не обробляти"
        second = document.add_table(rows=1, cols=2)
        second.cell(0, 0).text = "С. 1"
        second.cell(0, 0).add_paragraph("Текст")
        second.cell(0, 1).text = "С. 2"
        second.cell(0, 1).add_paragraph("Текст")
        raw = io.BytesIO()
        document.save(raw)

        assert inspect_tables(raw.getvalue()) == (
            type(inspect_tables(raw.getvalue())[0])(0, 1, 1),
            type(inspect_tables(raw.getvalue())[1])(1, 1, 1),
        )
        result = process_document(raw.getvalue(), HighlightOptions(table_index=1))
        output = Document(io.BytesIO(result.document_bytes))
        assert output.tables[0].cell(0, 0).text == "Не обробляти"
        assert result.stats.processed_rows == 1

    def test_rejects_document_without_table(self):
        document = Document()
        document.add_paragraph("Текст")
        raw = io.BytesIO()
        document.save(raw)
        with pytest.raises(DocumentValidationError, match="не містить таблиць"):
            process_document(raw.getvalue(), HighlightOptions())
