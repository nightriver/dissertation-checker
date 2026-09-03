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


_INSTITUTION_LINE_RE = re.compile(
    r"^(?:заклад вищої освіти|вищий навчальний заклад)\b", re.IGNORECASE,
)
_UDK_RE = re.compile(r"^УДК\b", re.IGNORECASE)
_TITLE_END_RE = re.compile(
    r"^(?:спеціальн\w*\b|галузь знань\b|подається\b|на здобуття\b"
    r"|дисертаці\w*\b|автореферат\b|науковий (?:керівник|консультант)\b"
    r"|кандидат\w*\b|доктор\w*\b|©"
    r"|\d{2}\.\d{2}\.\d{2}\b|\d{3}\s*[–—-])",
    re.IGNORECASE,
)
_CITY_OR_YEAR_RE = re.compile(r"^(?:.*[–—-]\s*)?(?:19[89]\d|20[0-2]\d)(?:\s*р(?:ік|\.)?)?$")


def _title_lines(document: SearchDocument) -> list[LineItem]:
    title_sections = {
        section.section_id for section in document.sections if section.kind == SectionKind.TITLE
    }
    return [
        {"line": " ".join(line.split()), "page": block.physical_page}
        for block in document.blocks
        if block.section_id in title_sections
        for line in block.raw_text.splitlines()
        if line.strip()
    ]


def _author_end(lines: list[LineItem], author: str | None) -> int | None:
    """Кінець ПІБ, зокрема прізвища та імені на окремих рядках."""
    if author is None:
        return None
    for start in range(len(lines)):
        for end in (start + 1, start + 2):
            candidate = " ".join(item["line"] for item in lines[start:end])
            if candidate.casefold() == author.casefold():
                return end
    return None


def _extract_title(lines: list[LineItem], author: str | None) -> str | None:
    """Зібрати назву після автора до спеціальності або службового блоку."""
    start = _author_end(lines, author)
    if start is None:
        start = next((
            index + 1 for index, item in enumerate(lines)
            if _UDK_RE.match(item["line"])
        ), None)
    if start is None:
        return None

    title = ""
    for item in lines[start:]:
        text = item["line"]
        # Окреме «ДИСЕРТАЦІЯ» буває і перед назвою, і після неї.
        dissertation_label = "".join(text.split()).casefold() == "дисертація"
        if _UDK_RE.match(text) or dissertation_label:
            if title:
                break
            continue
        if _TITLE_END_RE.match(text) or _CITY_OR_YEAR_RE.fullmatch(text) or text.isdigit():
            break
        if not any(char.isalpha() for char in text):
            continue
        # Зберегти дефіс у складних словах на межі рядків: порівняльно-правове.
        separator = "" if not title or title.endswith(("-", "\u00ad")) else " "
        title = title.removesuffix("\u00ad") + separator + text
    return title or None


def extract_search_metadata(document: SearchDocument) -> DissertationMetadata:
    """Взяти автора, назву й рік лише з титульної частини документа."""

    lines = _title_lines(document)
    year = extract_dissertation_year(lines)
    for page in dict.fromkeys(item["page"] for item in lines):
        page_lines = [item for item in lines if item["page"] == page]
        # Службовий заголовок із трьох слів не повинен ставати ПІБ, коли
        # PDF-парсер розмістив УДК окремим блоком наприкінці сторінки.
        author_lines = []
        for item in page_lines:
            if re.match(r"науковий (керівник|консультант)\b", item["line"], re.IGNORECASE):
                break
            if not _INSTITUTION_LINE_RE.match(item["line"]):
                author_lines.append(item)
        author = extract_dissertation_author(author_lines)
        title = _extract_title(page_lines, author)
        if author or title:
            return DissertationMetadata(author=author, title=title, year=year)
    return DissertationMetadata(year=year)
