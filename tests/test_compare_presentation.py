from compare.matcher import compare_tokens
from compare.normalize import tokenize_lines
from compare.presentation import (
    DIFF_COLOR,
    MATCH_COLOR,
    SIDE_A_TITLE,
    SIDE_B_TITLE,
    STACK_BREAKPOINT_PX,
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
    # Без міток рядок не тягне за собою порожній підпис.
    assert '<span class="compare-note">' not in table


def test_table_never_scrolls_horizontally():
    """
    Службові поля живуть у шапці рядка, тому ширина не фіксується.

    Повзунок горизонтальної прокрутки лежав під усіма знахідками і на
    довгій таблиці був недосяжним; тепер прокручуватись просто нічому.
    """
    tokens = tokenize_lines(_lines(" ".join(f"слово{index}" for index in range(30))))
    lines = _lines(" ".join(f"слово{index}" for index in range(30)))
    segment = compare_tokens(tokens, tokens).segments[0]
    table = render_comparison_table([segment], lines, tokens, lines, tokens)
    assert "overflow-x" not in table
    assert "min-width" not in table
    # Довгий нерозривний токен не має розпирати колонку.
    assert "overflow-wrap:anywhere" in table


def test_table_head_is_sticky_and_stacks_on_narrow_screen():
    tokens = tokenize_lines(_lines(" ".join(f"слово{index}" for index in range(30))))
    lines = _lines(" ".join(f"слово{index}" for index in range(30)))
    segment = compare_tokens(tokens, tokens).segments[0]
    table = render_comparison_table([segment], lines, tokens, lines, tokens)
    assert "position:sticky" in table
    assert f"@media (max-width:{STACK_BREAKPOINT_PX}px)" in table
    # У стеку шапка схована, тому кожна половина підписана сама.
    assert f'data-side="{SIDE_A_TITLE}"' in table
    assert f'data-side="{SIDE_B_TITLE}"' in table
    assert "content:attr(data-side)" in table


def test_pdf_word_per_line_is_rendered_horizontally():
    lines = [{"line": f"слово{index}", "page": 1} for index in range(15)]
    tokens = tokenize_lines(lines)
    segment = compare_tokens(tokens, tokens).segments[0]
    rendered = render_fragment_html(lines, tokens, segment.a_start, segment.a_end, segment.a_spans)
    assert "<br>" not in rendered
    assert "слово0" in rendered and "слово14" in rendered


def test_long_segment_is_collapsible_in_table():
    text = " ".join(f"довгий{index}" for index in range(140))
    lines = _lines(text)
    tokens = tokenize_lines(lines)
    segment = compare_tokens(tokens, tokens).segments[0]
    table = render_comparison_table([segment], lines, tokens, lines, tokens)
    assert "<details>" in table
    assert "compare-full-fragment" in table
