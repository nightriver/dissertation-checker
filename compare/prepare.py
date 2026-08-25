"""Підготовка структурних зон документа до текстового порівняння."""

from __future__ import annotations

import re

from compare.normalize import tokenize_lines
from compare.types import ExcludedRange, PreparedDocument
from parser.types import LineItem, is_toc_entry


_SPACE_RE = re.compile(r"\s+")
_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")


def _heading_kind(text: str) -> str | None:
    normalized = _SPACE_RE.sub(" ", text.strip().upper())
    if normalized == "ВСТУП":
        return "intro"
    if re.match(r"^РОЗДІЛ\s+\d+\b", normalized):
        return "chapter"
    if normalized.startswith("ВИСНОВКИ"):
        return "conclusions"
    return None


def _is_toc_header(text: str) -> bool:
    letters_only = re.sub(r"[^А-ЯЁЇІЄҐA-Z]", "", text.upper())
    return letters_only == "ЗМІСТ"


def _next_nonempty_text(lines: list[LineItem], index: int) -> str:
    for item in lines[index + 1:]:
        text = (item.get("line") or "").strip()
        if text:
            return text
    return ""


def _is_toc_heading(lines: list[LineItem], index: int) -> bool:
    """Підтримує PDF, де номер сторінки винесено в окремий рядок."""
    text = lines[index].get("line") or ""
    return is_toc_entry(text) or bool(_PAGE_NUMBER_RE.fullmatch(_next_nonempty_text(lines, index)))


def _find_content_line(lines: list[LineItem]) -> int | None:
    """Знаходить справжній ВСТУП, навіть якщо записи ЗМІСТ перенесені."""
    intros = [
        index for index, item in enumerate(lines)
        if _heading_kind(item.get("line") or "") == "intro"
    ]
    if not intros:
        return None
    toc_header = next(
        (
            index
            for index, item in enumerate(lines[:intros[0]])
            if _is_toc_header(item.get("line") or "")
        ),
        None,
    )
    if toc_header is not None:
        after_header = [index for index in intros if index > toc_header]
        if len(after_header) >= 2:
            return after_header[-1]
        if len(after_header) == 1 and not _is_toc_heading(lines, after_header[0]):
            return after_header[0]
        return None
    return next((index for index in intros if not _is_toc_heading(lines, index)), None)


def resembles_dissertation(lines: list[LineItem]) -> bool:
    content_line = _find_content_line(lines)
    if content_line is None:
        return False
    kinds = {
        kind
        for index, item in enumerate(lines[content_line:], content_line)
        if not _is_toc_heading(lines, index)
        if (kind := _heading_kind(item.get("line") or ""))
    }
    return {"intro", "chapter", "conclusions"}.issubset(kinds)


def _token_boundary(tokens, line_index: int) -> int:
    for index, token in enumerate(tokens):
        if token.parts[0].line_index >= line_index:
            return index
    return len(tokens)


def prepare_document(lines: list[LineItem]) -> PreparedDocument:
    """Виключає титул/ЗМІСТ лише у файлу з надійною структурою дисертації."""
    tokens = tokenize_lines(lines)
    if not resembles_dissertation(lines):
        return PreparedDocument(tokens, tokens, (), False)

    content_line = _find_content_line(lines)
    assert content_line is not None  # гарантує resembles_dissertation вище
    toc_line = next(
        (
            index for index, item in enumerate(lines[:content_line])
            if _is_toc_header(item.get("line") or "")
        ),
        None,
    )
    if toc_line is None:
        toc_line = next(
            (
                index for index, item in enumerate(lines[:content_line])
                if _heading_kind(item.get("line") or "") and _is_toc_heading(lines, index)
            ),
            None,
        )
    content_token = _token_boundary(tokens, content_line)
    excluded: list[ExcludedRange] = []
    if toc_line is None:
        if content_token:
            excluded.append(ExcludedRange(0, content_token, "title_page"))
    else:
        toc_token = _token_boundary(tokens, toc_line)
        if toc_token:
            excluded.append(ExcludedRange(0, toc_token, "title_page"))
        if content_token > toc_token:
            excluded.append(ExcludedRange(toc_token, content_token, "toc"))
    return PreparedDocument(tokens[content_token:], tokens, tuple(excluded), True)


def prepare_document_for_comparison(lines: list[LineItem]):
    """Додатково виносить надійно розпізнану бібліографію в окремий прохід."""
    from parser.bibliography import (
        BibliographyNotFoundError,
        MIN_BIBLIO_ENTRIES,
        parse_bibliography,
        split_zones,
    )

    prepared = prepare_document(lines)
    entries = {}
    try:
        zones = split_zones(lines)
        entries = parse_bibliography(zones.bibliography)
    except BibliographyNotFoundError:
        zones = None
    if zones is not None and len(entries) < MIN_BIBLIO_ENTRIES:
        prepared.bibliography_warning = (
            f"Список літератури розпізнано невпевнено: знайдено {len(entries)} "
            f"записів із мінімальних {MIN_BIBLIO_ENTRIES}; його залишено в текстовому порівнянні."
        )
    if zones is not None and len(entries) >= MIN_BIBLIO_ENTRIES:
        start_line = len(zones.body)
        end_line = start_line + len(zones.bibliography)
        start_token = _token_boundary(prepared.all_tokens, start_line)
        end_token = _token_boundary(prepared.all_tokens, end_line)
        prepared.tokens = [
            token for token in prepared.tokens
            if not (start_line <= token.parts[0].line_index < end_line)
        ]
        if end_token > start_token:
            prepared.excluded += (ExcludedRange(start_token, end_token, "bibliography"),)
    return prepared, entries
