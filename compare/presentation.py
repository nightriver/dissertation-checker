"""Безпечна HTML-презентація кольорових фрагментів."""

from __future__ import annotations

import html
from collections.abc import Sequence

from compare.types import CompareToken, DiffSpan, TextSegment
from parser.types import LineItem


MATCH_COLOR = "#fff59d"
DIFF_COLOR = "#b2ebf2"
MAX_INLINE_SEGMENT_TOKENS = 120


def format_physical_pages(tokens: Sequence[CompareToken], start: int, end: int) -> str:
    pages = sorted({
        page for token in tokens[start:end] for page in token.physical_pages
        if page is not None
    })
    if not pages:
        return "—"
    ranges: list[str] = []
    first = last = pages[0]
    for page in pages[1:]:
        if page == last + 1:
            last = page
        else:
            ranges.append(f"{first}–{last}" if last > first else str(first))
            first = last = page
    ranges.append(f"{first}–{last}" if last > first else str(first))
    return "аркуш PDF " + ", ".join(ranges)


def _span_operations(spans: Sequence[DiffSpan]) -> dict[int, str]:
    return {
        index: span.operation
        for span in spans
        for index in range(span.start_token, span.end_token)
    }


def _styled(value: str, operation: str | None) -> str:
    escaped = html.escape(value)
    if operation is None:
        return escaped
    if operation in {"equal", "fuzzy"}:
        decoration = "text-decoration:underline dashed" if operation == "fuzzy" else ""
        title = "близька словоформа" if operation == "fuzzy" else "точний збіг"
        return (
            f'<span title="{title}" style="background:{MATCH_COLOR};{decoration}">'
            f"{escaped}</span>"
        )
    return f'<span title="відмінність" style="background:{DIFF_COLOR}">{escaped}</span>'


def _line_separator(
    lines: Sequence[LineItem], previous_line: int, current_line: int, split_word: bool
) -> str:
    previous_page = lines[previous_line].get("page")
    current_page = lines[current_line].get("page")
    has_blank_line = any(
        not (lines[index].get("line") or "").strip()
        for index in range(previous_line + 1, current_line)
    )
    if split_word or has_blank_line or (
        previous_page is not None and current_page is not None and previous_page != current_page
    ):
        return "<br>"
    return " "


def render_fragment_html(
    lines: Sequence[LineItem],
    tokens: Sequence[CompareToken],
    start: int,
    end: int,
    spans: Sequence[DiffSpan],
    context_tokens: int = 8,
) -> str:
    """Повертає оригінальний текст із пунктуацією та контрольованими span."""
    if not tokens or start >= end:
        return ""
    visible_start = max(0, start - context_tokens)
    visible_end = min(len(tokens), end + context_tokens)
    operations = _span_operations(spans)
    output: list[str] = []
    previous_line: int | None = None
    previous_end = 0
    previous_operation: str | None = None
    for token_index in range(visible_start, visible_end):
        token = tokens[token_index]
        operation = operations.get(token_index)
        for part_index, part in enumerate(token.parts):
            text = lines[part.line_index].get("line") or ""
            if previous_line is None:
                prefix_start = 0
                if visible_start > 0:
                    previous_token = tokens[visible_start - 1]
                    previous_part = previous_token.parts[-1]
                    if previous_part.line_index == part.line_index:
                        prefix_start = previous_part.char_end
                        output.append("…")
                output.append(html.escape(text[prefix_start:part.char_start]))
            elif part.line_index == previous_line:
                gap = text[previous_end:part.char_start]
                output.append(_styled(gap, operation if operation == previous_operation else None))
            else:
                previous_text = lines[previous_line].get("line") or ""
                output.append(html.escape(previous_text[previous_end:]))
                output.append(_line_separator(lines, previous_line, part.line_index, part_index > 0))
                output.append(html.escape(text[:part.char_start]))
            output.append(_styled(text[part.char_start:part.char_end], operation))
            previous_line = part.line_index
            previous_end = part.char_end
            previous_operation = operation
    if previous_line is not None:
        last_text = lines[previous_line].get("line") or ""
        suffix_end = len(last_text)
        if visible_end < len(tokens):
            next_part = tokens[visible_end].parts[0]
            if next_part.line_index == previous_line:
                suffix_end = next_part.char_start
        output.append(html.escape(last_text[previous_end:suffix_end]))
        if visible_end < len(tokens):
            output.append("…")
    return "".join(output)


def _render_collapsible_fragment(
    lines: Sequence[LineItem],
    tokens: Sequence[CompareToken],
    start: int,
    end: int,
    spans: Sequence[DiffSpan],
) -> str:
    full = render_fragment_html(lines, tokens, start, end, spans)
    if end - start <= MAX_INLINE_SEGMENT_TOKENS:
        return full
    preview_size = MAX_INLINE_SEGMENT_TOKENS // 2
    beginning = render_fragment_html(lines, tokens, start, start + preview_size, spans, 0)
    ending = render_fragment_html(lines, tokens, end - preview_size, end, spans, 0)
    return (
        f'<details><summary>{beginning} … {ending}</summary>'
        f'<div class="compare-full-fragment">{full}</div></details>'
    )


def render_comparison_table(
    segments: Sequence[TextSegment],
    lines_a: Sequence[LineItem], tokens_a: Sequence[CompareToken],
    lines_b: Sequence[LineItem], tokens_b: Sequence[CompareToken],
) -> str:
    rows: list[str] = []
    for number, segment in enumerate(segments, 1):
        left = _render_collapsible_fragment(
            lines_a, tokens_a, segment.a_start, segment.a_end, segment.a_spans
        )
        right = _render_collapsible_fragment(
            lines_b, tokens_b, segment.b_start, segment.b_end, segment.b_spans
        )
        place = html.escape(
            f"{format_physical_pages(tokens_a, segment.a_start, segment.a_end)} / "
            f"{format_physical_pages(tokens_b, segment.b_start, segment.b_end)}"
        )
        kind = "дослівний" if segment.kind == "verbatim" else "змінений"
        labels = []
        if segment.possibly_normative:
            labels.append("ймовірно нормативний")
        if segment.possibly_boilerplate:
            labels.append("типова формула")
        label_html = f"<small>{html.escape(' · '.join(labels))}</small>" if labels else ""
        kind_html = kind + (f"<br>{label_html}" if label_html else "")
        indicators = html.escape(
            f"{segment.matched} слів · {segment.coverage_a:.0%}/{segment.coverage_b:.0%} · "
            f"схожість {segment.similarity:.0%}"
        )
        rows.append(
            f"<tr><td>{number}</td><td>{left}</td><td>{right}</td><td>{place}</td>"
            f"<td>{kind_html}</td><td>{indicators}</td></tr>"
        )
    return (
        '<style>'
        '.compare-table{overflow-x:auto;margin:0.5rem 0 1rem;}'
        '.compare-table table{border-collapse:collapse;min-width:1050px;width:100%;font-size:.92rem;}'
        '.compare-table th,.compare-table td{border:1px solid rgba(128,128,128,.35);padding:.55rem;vertical-align:top;line-height:1.45;}'
        '.compare-table th{position:sticky;top:0;background:var(--background-color,#fff);text-align:left;}'
        '.compare-table th:nth-child(1){width:3rem}.compare-table th:nth-child(2),.compare-table th:nth-child(3){width:32%;}'
        '.compare-table details summary{cursor:pointer}.compare-full-fragment{margin-top:.6rem;padding-top:.6rem;border-top:1px dashed #aaa;}'
        '</style><div class="compare-table"><table><thead><tr><th>№</th>'
        '<th>Перевірювана дисертація</th><th>Ймовірне джерело</th><th>Місце</th>'
        '<th>Тип</th><th>Показники</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )
