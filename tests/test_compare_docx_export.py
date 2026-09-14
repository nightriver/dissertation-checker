from io import BytesIO

from docx import Document
from docx.enum.text import WD_UNDERLINE
from docx.oxml.ns import qn

from compare.docx_export import DOCUMENT_TITLE, build_comparison_docx
from compare.matcher import compare_tokens
from compare.normalize import tokenize_lines
from compare.presentation import (
    DIFF_COLOR,
    LINE_BREAK,
    MATCH_COLOR,
    SIDE_A_TITLE,
    SIDE_B_TITLE,
    fragment_pieces,
)


def _lines(text, page=None):
    return [{"line": text, "page": page}]


def _pair():
    base = [f"термін{i}" for i in range(30)]
    changed = base[:15] + ["заміна"] + base[16:]
    lines_a, lines_b = _lines(" ".join(base), 3), _lines(" ".join(changed), 7)
    tokens_a, tokens_b = tokenize_lines(lines_a), tokenize_lines(lines_b)
    segments = compare_tokens(tokens_a, tokens_b).segments
    return segments, lines_a, tokens_a, lines_b, tokens_b


def _open(data):
    return Document(BytesIO(data))


def _fills(cell):
    return {
        shading.get(qn("w:fill"))
        for shading in cell._tc.iter(qn("w:shd"))
    }


def test_docx_has_header_meta_row_and_two_text_columns():
    segments, lines_a, tokens_a, lines_b, tokens_b = _pair()
    data = build_comparison_docx(
        segments, lines_a, tokens_a, lines_b, tokens_b,
        name_a="дисертація.pdf", name_b="джерело.docx", summary=["Знайдені фрагменти: 1"],
    )
    document = _open(data)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert DOCUMENT_TITLE in text
    assert "дисертація.pdf" in text and "джерело.docx" in text
    assert "Знайдені фрагменти: 1" in text

    table = document.tables[0]
    assert [cell.text for cell in table.rows[0].cells] == [SIDE_A_TITLE, SIDE_B_TITLE]
    # Шапка повторюється на кожній сторінці.
    assert table.rows[0]._tr.find(qn("w:trPr")).find(qn("w:tblHeader")) is not None
    # Шапка + (рядок показників, рядок текстів) на кожну знахідку.
    assert len(table.rows) == 1 + 2 * len(segments)
    meta = table.rows[1].cells
    assert meta[0]._tc is meta[1]._tc
    assert "аркуш PDF 3 / аркуш PDF 7" in meta[0].text
    assert "змінений" in meta[0].text
    left, right = table.rows[2].cells
    assert "термін0" in left.text and "заміна" not in left.text
    assert "заміна" in right.text


def test_docx_highlights_use_screen_colors():
    segments, lines_a, tokens_a, lines_b, tokens_b = _pair()
    table = _open(build_comparison_docx(
        segments, lines_a, tokens_a, lines_b, tokens_b, name_a="a", name_b="b",
    )).tables[0]
    fills = _fills(table.rows[2].cells[1])
    assert MATCH_COLOR.lstrip("#").upper() in fills
    assert DIFF_COLOR.lstrip("#").upper() in fills


def test_docx_keeps_raw_text_unescaped_and_long_fragment_whole():
    """HTML-екранування у Word не потрібне, а довгий фрагмент не згортається."""
    words = [f"слово{i}" for i in range(200)]
    lines = _lines("<script> " + " ".join(words) + " & кінець")
    tokens = tokenize_lines(lines)
    segments = compare_tokens(tokens, tokens).segments
    table = _open(build_comparison_docx(
        segments, lines, tokens, lines, tokens, name_a="a", name_b="b",
    )).tables[0]
    cell_text = table.rows[2].cells[0].text
    assert "&lt;" not in cell_text
    assert "слово0" in cell_text and "слово199" in cell_text


def test_docx_marks_fuzzy_with_dashed_underline_and_keeps_line_breaks():
    lines_a = [
        {"line": " ".join(f"слово{i}" for i in range(12)), "page": 1},
        {"line": " ".join(f"слово{i}" for i in range(12, 24)), "page": 2},
    ]
    tokens_a = tokenize_lines(lines_a)
    segments = compare_tokens(tokens_a, tokens_a).segments
    pieces = fragment_pieces(
        lines_a, tokens_a, segments[0].a_start, segments[0].a_end, segments[0].a_spans
    )
    # Перехід на інший аркуш PDF — розрив рядка, як <br> на екрані.
    assert ("", LINE_BREAK) in pieces
    table = _open(build_comparison_docx(
        segments, lines_a, tokens_a, lines_a, tokens_a, name_a="a", name_b="b",
    )).tables[0]
    assert table.rows[2].cells[0]._tc.iter(qn("w:br")).__next__() is not None

    from compare.docx_export import _fill_fragment

    paragraph = Document().add_paragraph()
    _fill_fragment(paragraph, [("близько", "fuzzy"), ("", "equal"), ("інше", "insert")])
    assert [run.text for run in paragraph.runs] == ["близько", "інше"]
    assert paragraph.runs[0].font.underline == WD_UNDERLINE.DASH
    assert paragraph.runs[1].font.underline is None


def test_docx_without_findings_says_so():
    document = _open(build_comparison_docx([], [], [], [], [], name_a="a", name_b="b"))
    assert len(document.tables[0].rows) == 1
    assert "Знахідок немає." in [paragraph.text for paragraph in document.paragraphs]
