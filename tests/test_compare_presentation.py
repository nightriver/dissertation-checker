from compare.matcher import compare_tokens
from compare.normalize import tokenize_lines
from compare.presentation import (
    DIFF_COLOR,
    MATCH_COLOR,
    format_physical_pages,
    render_comparison_table,
    render_fragment_html,
)


def _lines(text, page=None):
    return [{"line": text, "page": page}]


def test_docx_location_is_dash_and_pdf_is_physical_sheet():
    docx_tokens = tokenize_lines(_lines("текст", None))
    pdf_tokens = tokenize_lines(_lines("текст", 4))
    assert format_physical_pages(docx_tokens, 0, 1) == "—"
    assert format_physical_pages(pdf_tokens, 0, 1) == "аркуш PDF 4"


def test_document_html_is_escaped_and_punctuation_is_preserved():
    words = [f"слово{i}" for i in range(15)]
    text = "<script>" + ", ".join(words) + " & кінець"
    lines = _lines(text)
    tokens = tokenize_lines(lines)
    result = compare_tokens(tokens, tokens)
    segment = result.segments[0]
    rendered = render_fragment_html(lines, tokens, segment.a_start, segment.a_end, segment.a_spans)
    assert "<script>" not in rendered
    assert "&lt;" in rendered and "&gt;" in rendered
    assert ", " in rendered
    assert MATCH_COLOR in rendered


def test_table_contains_legend_colors_and_escaped_cells():
    base = [f"термін{i}" for i in range(30)]
    changed = base[:15] + ["заміна"] + base[16:]
    lines_a, lines_b = _lines(" ".join(base)), _lines(" ".join(changed))
    tokens_a, tokens_b = tokenize_lines(lines_a), tokenize_lines(lines_b)
    segment = compare_tokens(tokens_a, tokens_b).segments[0]
    table = render_comparison_table([segment], lines_a, tokens_a, lines_b, tokens_b)
    assert MATCH_COLOR in table
    assert DIFF_COLOR in table
    assert "змінений" in table
