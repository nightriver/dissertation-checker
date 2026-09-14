"""Вивантаження таблиці текстових збігів у документ MS Word (.docx)."""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_UNDERLINE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from compare.presentation import (
    DIFF_COLOR,
    LINE_BREAK,
    MATCH_COLOR,
    SIDE_A_TITLE,
    SIDE_B_TITLE,
    finding_meta,
    fragment_pieces,
)
from compare.types import CompareToken, TextSegment
from parser.types import LineItem


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCUMENT_TITLE = "Порівняння двох робіт: текстові збіги"
META_FILL = "E7E7E7"
HEAD_FILL = "D0D0D0"
FONT_SIZE_PT = 10


def _hex(color: str) -> str:
    return color.lstrip("#").upper()


def _shade(element, fill: str) -> None:
    """Заливка комірки (tcPr) або тексту run (rPr) — однаковий w:shd."""
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    element.append(shading)


def _mark_header_row(row) -> None:
    """Шапка таблиці повторюється на кожній сторінці Word."""
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(header)


def _fill_fragment(paragraph, pieces: Sequence[tuple[str, str | None]]) -> None:
    for text, operation in pieces:
        if operation == LINE_BREAK:
            paragraph.add_run().add_break()
            continue
        if not text:
            continue
        run = paragraph.add_run(text)
        if operation is None:
            continue
        if operation in {"equal", "fuzzy"}:
            _shade(run._r.get_or_add_rPr(), _hex(MATCH_COLOR))
            if operation == "fuzzy":
                run.font.underline = WD_UNDERLINE.DASH
        else:
            _shade(run._r.get_or_add_rPr(), _hex(DIFF_COLOR))


def build_comparison_docx(
    segments: Sequence[TextSegment],
    lines_a: Sequence[LineItem], tokens_a: Sequence[CompareToken],
    lines_b: Sequence[LineItem], tokens_b: Sequence[CompareToken],
    *,
    name_a: str,
    name_b: str,
    summary: Sequence[str] = (),
) -> bytes:
    """
    Будує .docx з тими самими знахідками, що й таблиця на екрані.

    Довгі фрагменти не згортаються: у документі немає «розгорнути», тому
    текст іде повністю разом із контекстом. Підсвічування — заливкою тексту
    тими самими кольорами, що й на екрані; близька словоформа ще й
    підкреслена штрихом.
    """
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(1.5))
    document.styles["Normal"].font.size = Pt(FONT_SIZE_PT)

    document.add_heading(DOCUMENT_TITLE, level=1)
    document.add_paragraph(f"{SIDE_A_TITLE}: {name_a}")
    document.add_paragraph(f"{SIDE_B_TITLE}: {name_b}")
    for line in summary:
        document.add_paragraph(line)
    legend = document.add_paragraph("Позначення: ")
    _fill_fragment(legend, [
        ("збігається", "equal"), (" · ", None),
        ("близька словоформа", "fuzzy"), (" · ", None),
        ("відрізняється", "replace"),
    ])

    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    head = table.rows[0]
    _mark_header_row(head)
    for cell, title in zip(head.cells, (SIDE_A_TITLE, SIDE_B_TITLE)):
        cell.text = ""
        cell.paragraphs[0].add_run(title).bold = True
        _shade(cell._tc.get_or_add_tcPr(), HEAD_FILL)

    for number, segment in enumerate(segments, 1):
        meta = finding_meta(segment, tokens_a, tokens_b)
        meta_cell = table.add_row().cells[0].merge(table.rows[-1].cells[1])
        paragraph = meta_cell.paragraphs[0]
        paragraph.add_run(f"{number}").bold = True
        parts = [meta.place, meta.kind, meta.indicators, *meta.labels]
        paragraph.add_run("   " + " · ".join(parts))
        _shade(meta_cell._tc.get_or_add_tcPr(), META_FILL)

        pair = table.add_row().cells
        _fill_fragment(pair[0].paragraphs[0], fragment_pieces(
            lines_a, tokens_a, segment.a_start, segment.a_end, segment.a_spans
        ))
        _fill_fragment(pair[1].paragraphs[0], fragment_pieces(
            lines_b, tokens_b, segment.b_start, segment.b_end, segment.b_spans
        ))

    if not segments:
        document.add_paragraph("Знахідок немає.")

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
