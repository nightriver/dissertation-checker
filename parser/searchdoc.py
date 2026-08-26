"""
parser/searchdoc.py
Витяг структури PDF для режиму ручного пошуку джерел: блоки, колонтитули,
розділи, зони та охоплення. Специфікація — PLAN_SEARCH.md, §5.

Крок 3 (§22) реалізує лише мінімальну структуру, достатню для
односторінкового PDF з текстовим шаром і одним розділом: один блок
`get_text("dict")` = один `SearchBlock` (без злиття рядків за поріговими
коефіцієнтами §5.2, без колонок і без видалення колонтитулів §5.3).
Бібліографія і цитування — валідні порожні колекції (§22, крок 3).
Повний конвеєр §5.2–§5.4 (пороги злиття, колонки, колонтитури, `EXPECTED_SPARSE`,
`SectionOverride`) — крок 5.

Навмисно НЕ використовується `page.get_text("blocks")` (спрощене
кортеж-API): воно тут не потрібне і непридатне для абзаців (див. docstring
`parser.paragraph_analyzer._extract_paragraphs_pdf`). Використовується
`page.get_text("dict", sort=True)` — саме так, як і вимагає §5.1.
"""

from __future__ import annotations

import hashlib
import re

from search.normalization import normalize_text, tokenize
from search.sentences import split_sentences
from search.types import (
    CONTENT_SECTION_KINDS,
    Confidence,
    PageInfo,
    PageTextState,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SectionInfo,
    SectionKind,
    SentenceDonor,
    SourceSpan,
    TextZone,
    ZoneSpan,
)

# Змінюється, якщо той самий PDF може дати інші блоки, розділи, зони,
# речення чи вихідні координати (PLAN_SEARCH.md, §4.1).
PARSER_VERSION = "searchdoc-parser-2026-08-25"


class NoTextLayerError(Exception):
    """У PDF немає жодного текстового символу; OCR не запускається (§5.4)."""


_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]")

# §5.4: після видалення колонтитулів NO_TEXT/LOW_TEXT/TEXT_OK. У кроці 3
# колонтитури ще не видаляються (крок 5), тому пороги застосовуються до
# всього тексту сторінки.
_TEXT_OK_MIN_CHARS = 200

# "Тіло роботи": extractable_body_pages — сторінки з ≥20 літерами Unicode
# або ≥5 словесними токенами (§5.4).
_MIN_EXTRACTABLE_LETTERS = 20
_MIN_EXTRACTABLE_WORDS = 5

# ТИМЧАСОВА евристика кроку 3: числа 12 немає в §6.1 (довжина заголовка
# планом не задана). CLAUDE.md, правило №8 забороняє вигадувати пороги —
# питання винесене оркестратору. Повна розмітка розділів (крок 5) або
# замінить це число обґрунтованим, або приймe явне рішення користувача.
_MAX_HEADING_WORDS = 12
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], SectionKind], ...] = (
    (re.compile(r"^ВСТУП\s*$", re.IGNORECASE | re.UNICODE), SectionKind.INTRO),
    (
        re.compile(r"^(ЗАГАЛЬНІ\s+)?ВИСНОВКИ\s*$", re.IGNORECASE | re.UNICODE),
        SectionKind.CONCLUSIONS,
    ),
    (
        re.compile(r"^РОЗДІЛ\s+([0-9]+|[IVXLCDM]+)\b.*$", re.IGNORECASE | re.UNICODE),
        SectionKind.CHAPTER,
    ),
)
_CHAPTER_ORDINAL_RE = re.compile(r"^РОЗДІЛ\s+(\d+)\b", re.IGNORECASE | re.UNICODE)


def parse_search_document(pdf_bytes: bytes) -> SearchDocument:
    """
    Будує `SearchDocument` з байтів PDF (§5.1). Підтримує мінімальний
    тонкий зріз: односторінковий PDF з текстовим шаром і одним розділом;
    повна поведінка для складних макетів — крок 5.
    """
    import fitz

    document_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    raw_page_blocks: list[dict] = []
    page_content_chars: dict[int, int] = {}

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        n_pages = doc.page_count
        for page_index in range(n_pages):
            physical_page = page_index + 1
            page = doc[page_index]
            text_dict = page.get_text("dict", sort=True)
            content_chars = 0
            for raw_block in text_dict.get("blocks", []):
                if raw_block.get("type") != 0:
                    continue
                lines_text: list[str] = []
                for line in raw_block.get("lines", []):
                    line_text = "".join(
                        span.get("text", "") for span in line.get("spans", [])
                    ).strip()
                    if line_text:
                        lines_text.append(line_text)
                if not lines_text:
                    continue
                block_raw_text = "\n".join(lines_text)
                content_chars += sum(1 for ch in block_raw_text if not ch.isspace())
                raw_page_blocks.append(
                    {"physical_page": physical_page, "raw_text": block_raw_text}
                )
            page_content_chars[physical_page] = content_chars

    if n_pages == 0 or sum(page_content_chars.values()) == 0:
        raise NoTextLayerError(
            "У PDF немає текстового шару. Розпізнавання сканів (OCR) не "
            "виконується — завантажте PDF із текстовим шаром."
        )

    blocks, sections = _build_blocks_and_sections(raw_page_blocks)
    sentences = _build_sentence_donors(blocks, document_sha256)

    pages = tuple(
        _build_page_info(physical_page, page_content_chars[physical_page])
        for physical_page in sorted(page_content_chars)
    )

    body_blocks = [b for b in blocks if _section_kind_by_id(sections, b.section_id) in CONTENT_SECTION_KINDS]
    expected_body_pages, extractable_body_pages = _page_coverage(body_blocks)
    coverage_ratio = (
        extractable_body_pages / expected_body_pages if expected_body_pages else 0.0
    )

    return SearchDocument(
        document_sha256=document_sha256,
        parser_version=PARSER_VERSION,
        n_pages=n_pages,
        pages=pages,
        expected_body_pages=expected_body_pages,
        extractable_body_pages=extractable_body_pages,
        coverage_ratio=coverage_ratio,
        blocks=tuple(blocks),
        sections=tuple(sections),
        sentences=sentences,
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.LOW,
        applied_overrides=(),
    )


def _section_kind_by_id(sections: list[SectionInfo], section_id: str) -> SectionKind:
    for section in sections:
        if section.section_id == section_id:
            return section.kind
    return SectionKind.UNKNOWN


def _classify_heading(text: str) -> SectionKind | None:
    stripped = text.strip()
    if not stripped or len(stripped.split()) > _MAX_HEADING_WORDS:
        return None
    for pattern, kind in _HEADING_PATTERNS:
        if pattern.match(stripped):
            return kind
    return None


def _chapter_ordinal(text: str) -> int | None:
    match = _CHAPTER_ORDINAL_RE.match(text.strip())
    return int(match.group(1)) if match else None


def _build_blocks_and_sections(
    raw_page_blocks: list[dict],
) -> tuple[list[SearchBlock], list[SectionInfo]]:
    # Крок A: розбиваємо вихідні блоки на суміжні сегменти-розділи за
    # заголовками (§6.1). Заголовок сам є першим членом свого сегмента.
    segments: list[dict] = []
    current: dict = {"kind": SectionKind.UNKNOWN, "heading": "", "ordinal": None, "members": []}
    for idx, raw_block in enumerate(raw_page_blocks):
        kind = _classify_heading(raw_block["raw_text"])
        if kind is not None:
            if current["members"]:
                segments.append(current)
            ordinal = _chapter_ordinal(raw_block["raw_text"]) if kind == SectionKind.CHAPTER else None
            current = {
                "kind": kind,
                "heading": raw_block["raw_text"].strip(),
                "ordinal": ordinal,
                "members": [idx],
            }
        else:
            current["members"].append(idx)
    if current["members"]:
        segments.append(current)

    # Крок B: будуємо SearchBlock для кожного вихідного блоку.
    blocks: list[SearchBlock] = []
    for seg_index, segment in enumerate(segments):
        section_id = f"sec-{seg_index:03d}"
        heading_path = (segment["heading"],) if segment["heading"] else ()
        for member_idx in segment["members"]:
            raw_block = raw_page_blocks[member_idx]
            raw_text = raw_block["raw_text"]
            normalized = normalize_text(raw_text)
            tokens = tokenize(raw_text, normalized)
            zone_spans = (
                (ZoneSpan(0, len(raw_text), TextZone.AUTHOR_TEXT, Confidence.MEDIUM, "thin_slice_default"),)
                if raw_text
                else ()
            )
            blocks.append(
                SearchBlock(
                    block_id=f"blk-{member_idx:05d}",
                    raw_text=raw_text,
                    normalized=normalized,
                    tokens=tokens,
                    section_id=section_id,
                    heading_path=heading_path,
                    physical_page=raw_block["physical_page"],
                    block_index=member_idx,
                    zone_spans=zone_spans,
                )
            )

    # Крок C: SectionInfo на основі вже побудованих блоків.
    sections: list[SectionInfo] = []
    for seg_index, segment in enumerate(segments):
        section_id = f"sec-{seg_index:03d}"
        member_blocks = [blocks[i] for i in range(len(blocks)) if blocks[i].section_id == section_id]
        physical_pages = tuple(sorted({b.physical_page for b in member_blocks}))
        author_words = sum(sum(1 for t in b.tokens if t.is_word) for b in member_blocks)
        expected, extractable = _page_coverage(member_blocks)
        coverage_ratio = extractable / expected if expected else 0.0
        member_indices = segment["members"]
        sections.append(
            SectionInfo(
                section_id=section_id,
                kind=segment["kind"],
                ordinal=segment["ordinal"],
                heading=segment["heading"],
                block_start=member_indices[0],
                block_end=member_indices[-1] + 1,
                physical_pages=physical_pages,
                author_words=author_words,
                expected_body_pages=expected,
                extractable_body_pages=extractable,
                coverage_ratio=coverage_ratio,
                confidence=Confidence.MEDIUM,
            )
        )

    return blocks, sections


def _page_coverage(section_blocks: list[SearchBlock]) -> tuple[int, int]:
    """§5.4: expected_body_pages / extractable_body_pages для набору блоків."""
    pages: dict[int, list[SearchBlock]] = {}
    for block in section_blocks:
        pages.setdefault(block.physical_page, []).append(block)
    expected = len(pages)
    extractable = 0
    for page_blocks in pages.values():
        letters = sum(len(_LETTER_RE.findall(b.raw_text)) for b in page_blocks)
        words = sum(sum(1 for t in b.tokens if t.is_word) for b in page_blocks)
        if letters >= _MIN_EXTRACTABLE_LETTERS or words >= _MIN_EXTRACTABLE_WORDS:
            extractable += 1
    return expected, extractable


def _build_page_info(physical_page: int, content_chars: int) -> PageInfo:
    if content_chars <= 0:
        state = PageTextState.NO_TEXT
        reason = "На сторінці не знайдено жодного текстового символу."
    elif content_chars < _TEXT_OK_MIN_CHARS:
        state = PageTextState.LOW_TEXT
        reason = f"Лише {content_chars} змістовних символів на сторінці."
    else:
        state = PageTextState.TEXT_OK
        reason = ""
    return PageInfo(
        physical_page=physical_page,
        content_chars=content_chars,
        author_words=0,
        large_raster_ratio=0.0,
        extractable=state in (PageTextState.TEXT_OK, PageTextState.LOW_TEXT),
        state=state,
        reason=reason,
    )


def _build_sentence_donors(
    blocks: list[SearchBlock], document_sha256: str
) -> tuple[SentenceDonor, ...]:
    donors: list[SentenceDonor] = []
    occurrence_counts: dict[tuple[int, str], int] = {}
    for block in blocks:
        if not block.raw_text:
            continue
        sentence_bounds = split_sentences(block.raw_text)
        for ordinal, (start, end) in enumerate(sentence_bounds):
            raw_text = block.raw_text[start:end]
            normalized_text = normalize_text(raw_text).text
            author_word_count = sum(
                1 for t in tokenize(raw_text, normalize_text(raw_text)) if t.is_word
            )
            key = (block.physical_page, normalized_text)
            occurrence_index = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occurrence_index + 1
            donor_id = hashlib.sha256(
                f"{document_sha256}|{block.physical_page}|{normalized_text}|{occurrence_index}".encode("utf-8")
            ).hexdigest()
            donors.append(
                SentenceDonor(
                    donor_id=donor_id,
                    block_id=block.block_id,
                    section_id=block.section_id,
                    sentence_ordinal=ordinal,
                    occurrence_index=occurrence_index,
                    source=SourceSpan(
                        parts=(RawSpan(block.block_id, block.physical_page, start, end),)
                    ),
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    author_word_count=author_word_count,
                )
            )
    return tuple(donors)
