"""Метадані перевірюваної роботи для режиму ручного пошуку (PLAN_SEARCH.md, §17)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from parser.extractor import extract_dissertation_author, extract_dissertation_year
from parser.types import LineItem
from search.types import SearchDocument, SectionKind


@dataclass(frozen=True)
class DissertationMetadata:
    author: str | None = None
    title: str | None = None
    year: int | None = None


_SERVICE_WORDS = (
    "удк", "дисертація", "автореферат", "на здобуття", "наукового ступеня",
    "спеціальністю", "науковий керівник", "міністерство", "університет",
    "інститут", "академія", "кафедра", "факультет",
)
_CITY_OR_YEAR_RE = re.compile(r"^(?:.*[–—-]\s*)?(?:19[89]\d|20[0-2]\d)(?:\s*р(?:ік|\.)?)?$")


def _title_lines(document: SearchDocument) -> list[LineItem]:
    return [
        {"line": line.strip(), "page": block.physical_page}
        for block in document.blocks
        if block.section_id in {
            section.section_id for section in document.sections if section.kind == SectionKind.TITLE
        }
        for line in block.raw_text.splitlines()
        if line.strip()
    ]


def _extract_title(lines: list[LineItem], author: str | None) -> str | None:
    candidates: list[str] = []
    author_upper = author.upper() if author else ""
    for item in lines:
        text = " ".join(item["line"].split())
        lower = text.lower()
        if author_upper and text.upper() == author_upper:
            continue
        if _CITY_OR_YEAR_RE.fullmatch(text) or any(word in lower for word in _SERVICE_WORDS):
            continue
        letter_count = sum(char.isalpha() for char in text)
        if letter_count >= 12 and len(text.split()) >= 2:
            candidates.append(text)
    return max(candidates, key=len) if candidates else None


def extract_search_metadata(document: SearchDocument) -> DissertationMetadata:
    """Взяти автора, назву й рік лише з титульної частини документа."""

    lines = _title_lines(document)
    author = extract_dissertation_author(lines)
    return DissertationMetadata(
        author=author,
        title=_extract_title(lines, author),
        year=extract_dissertation_year(lines),
    )
