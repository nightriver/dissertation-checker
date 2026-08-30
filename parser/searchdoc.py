"""
parser/searchdoc.py
Витяг структури PDF для режиму ручного пошуку джерел: блоки, колонки,
колонтитули, стани сторінок, розділи, зони та охоплення.
Специфікація — PLAN_SEARCH.md, §5 і §6 (крок 5 таблиці §22).

Конвеєр:

1. §5.1 — сторінка читається через `page.get_text("dict", sort=True)`;
   геометрія рядків, кегль і flags живуть тільки всередині модуля.
2. §5.2 — рядки збираються в абзаци за пороговими коефіцієнтами
   (`_LINE_MERGE_GAP_FACTOR`, `_LINE_MERGE_LEFT_SHIFT_FACTOR`,
   `_FONT_SIZE_BREAK_RATIO`), розпізнаються дві колонки
   (`_COLUMN_GAP_FACTOR`); текст через межу фізичного аркуша не склеюється.
3. §5.3 — повторювані колонтитули шукаються в смугах `_HEADER_FOOTER_BAND`
   і не потрапляють в авторський текст, але лишаються окремими блоками
   із зоною `HEADER_FOOTER` (CLAUDE.md, правило №3: нічого не зникає).
4. §5.4 — стан текстового шару кожної сторінки, `EXPECTED_SPARSE`
   для очікувано розріджених аркушів, охоплення за формулою.
5. §6.1 — карта розділів усіх дев'яти типів, відсічення записів ЗМІСТу,
   `SectionOverride` як повний перерахунок з нуля.
6. §6.2 і §4.1 — зони зберігаються інтервалами всередині блоку і
   зводяться за `search.types.ZONE_PRIORITY`.

Бібліографію і цитування наприкінці додає крок 6
(`search.bibliography.build_bibliography` і `build_citations`): вони —
чисті функції від уже готового документа, тому живуть окремим модулем.

Навмисно НЕ використовується `page.get_text("blocks")` (спрощене
кортеж-API): воно непридатне для абзаців (див. docstring
`parser.paragraph_analyzer._extract_paragraphs_pdf`) і заборонене
CLAUDE.md, правило №4. Навмисно НЕ використовується
`find_content_bounds_in_texts`: він викидає ВСТУП і ВИСНОВКИ, а тут це
повноцінні змістовні розділи.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from statistics import median

from parser.bibliography import BibliographyNotFoundError, split_zones
from parser.types import is_toc_entry
from search.bibliography import build_bibliography, build_citations
from search.normalization import normalize_text, tokenize
from search.sentences import split_sentences_detailed
from search.types import ZONE_PRIORITY as _ZONE_PRIORITY
from search.types import (
    CONTENT_SECTION_KINDS,
    Confidence,
    PageInfo,
    PageTextState,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchToken,
    SectionInfo,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
    SentenceDonor,
    SourceSpan,
    TextZone,
    ZoneSpan,
)

# Змінюється, якщо той самий PDF може дати інші блоки, розділи, зони,
# речення чи вихідні координати (PLAN_SEARCH.md, §4.1).
PARSER_VERSION = "searchdoc-parser-2026-08-30-baseline-order"


class NoTextLayerError(Exception):
    """У PDF немає жодного текстового символу; OCR не запускається (§5.4)."""


# ---------------------------------------------------------------------------
# Порогові коефіцієнти (пакет кроку 5, розділ «Числа»)
#
# Усі константи приватні навмисно: публічний контракт модуля — рівно
# `PARSER_VERSION`, `NoTextLayerError` і `parse_search_document`, і його
# стереже approval-тест `tests/test_project_rules.py`.
# ---------------------------------------------------------------------------

# §5.2, п.3 і п.4: вертикальний розрив між рядками одного абзацу.
_LINE_MERGE_GAP_FACTOR = 1.4
# §5.2, п.3: припустиме зміщення лівого краю сусідніх рядків абзацу.
_LINE_MERGE_LEFT_SHIFT_FACTOR = 0.08
# §5.2, п.4: зміна кегля, що починає новий блок.
_FONT_SIZE_BREAK_RATIO = 0.25
# §5.2, п.5: розрив між кластерами x0, що дає дві колонки.
_COLUMN_GAP_FACTOR = 0.25

# §5.3: верхня і нижня смуги сторінки, де шукаються колонтитули.
_HEADER_FOOTER_BAND = 0.12
# §5.3: частка придатних сторінок, на яких має траплятися рядок.
_HEADER_FOOTER_MIN_PAGE_RATIO = 0.60
# Рішення оркестратора, крок 5: на документі в одну-дві сторінки поріг
# 60 % позбавлений сенсу, тому колонтитули не шукаються взагалі.
_HEADER_FOOTER_MIN_PAGES = 3

# §5.4: стан текстового шару сторінки.
_TEXT_OK_MIN_CHARS = 200
_SPARSE_RASTER_RATIO = 0.50
_SPARSE_MAX_SHORT_BLOCKS = 2
# Рішення оркестратора, крок 5: «короткий блок вигляду заголовок/підпис/
# роздільник» — блок не довший за вісім слів.
_SHORT_BLOCK_MAX_WORDS = 8

# §5.4: критерій придатності сторінки до витягу.
_MIN_EXTRACTABLE_LETTERS = 20
_MIN_EXTRACTABLE_WORDS = 5
# §5.4: межа повного охоплення (використовує UI кроку 15).
_FULL_COVERAGE_RATIO = 0.9

# Рішення оркестратора, крок 5: заголовок довший за дванадцять слів
# заголовком не вважається, яким би шаблоном він не збігся.
_MAX_HEADING_WORDS = 12
# Рішення оркестратора, крок 5: блоки до першого розпізнаного заголовка
# отримують TITLE, якщо лежать на аркушах 1–2 PDF, інакше UNKNOWN.
_TITLE_MAX_PAGE = 2

# Рішення оркестратора, крок 5 (§6.2 вимагає «нижню зону сторінки» і
# «менший шрифт», не називаючи чисел): нижня чверть аркуша і кегль не
# більший за 0,9 медіанного кегля сторінки.
_FOOTNOTE_BAND = 0.25
_FOOTNOTE_FONT_RATIO = 0.90

# Рішення оркестратора, крок 5: ознака розрядки заголовка («В С Т У П») —
# серія з трьох і більше однолітерних токенів поспіль. Серія з двох не
# зводиться ніколи: дві однолітерні лексеми поспіль трапляються і в
# звичайному тексті, і в ініціалах.
_LETTER_SPACING_MIN_RUN = 3

# Рішення оркестратора, крок 5: технічна стабілізація порівняння float при
# групуванні кегля і кластерів `x0`. Це не доменні пороги.
_FONT_SIZE_ROUND_DIGITS = 2
_COORD_ROUND_DIGITS = 1


# ---------------------------------------------------------------------------
# Регулярні вирази
# ---------------------------------------------------------------------------

# Літера Unicode: будь-який буквений символ, окрім цифр і підкреслення.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Крапкові лідери запису ЗМІСТу і табуляція (§6.1, відсічення, п.1).
_DOT_LEADERS_RE = re.compile(r"\.{3,}|\t")
# Рядок закінчується числом — номер сторінки (§6.1, відсічення, п.2).
_TRAILING_NUMBER_RE = re.compile(r"\d+\s*$")

# Провідна нумерація «1.», «1.1.», «I.» перед ключовим словом.
_LEADING_ORDINAL = r"(?:(?:\d+(?:\.\d+)*|[IVXLCDMІХСМ]+)\.?\s+)?"
# Хвіст простого заголовка: тільки пробіли й пунктуація.
_PUNCT_TAIL = r"[\s.:;!—–\-]*$"
# Номер розділу: арабський або римський (латиниця плюс кириличні
# двійники І, Х, С, М, які реально трапляються в українських PDF).
_CHAPTER_NUMBER = r"(\d+|[IVXLCDMІХСМ]+)"

_SIMPLE_HEADING_FORMS: tuple[tuple[SectionKind, str], ...] = (
    (SectionKind.TOC, r"(?:ЗМІСТ|СОДЕРЖАНИЕ|CONTENTS)"),
    (SectionKind.ABSTRACT, r"(?:АНОТАЦІЯ|АННОТАЦИЯ|ABSTRACT|РЕФЕРАТ)"),
    (SectionKind.INTRO, r"(?:ВСТУП|ВВЕДЕННЯ|ВВЕДЕНИЕ)"),
    (
        SectionKind.CONCLUSIONS,
        r"(?:ЗАГАЛЬНІ\s+ВИСНОВКИ|ВИСНОВКИ|ВЫВОДЫ|ЗАКЛЮЧЕНИЕ)",
    ),
    (
        SectionKind.BIBLIO,
        r"(?:СПИСОК\s+ВИКОРИСТАНИХ\s+ДЖЕРЕЛ"
        r"|СПИСОК\s+ВИКОРИСТАНОЇ\s+ЛІТЕРАТУРИ"
        r"|СПИСОК\s+ЛІТЕРАТУРИ"
        r"|БІБЛІОГРАФІЯ"
        r"|СПИСОК\s+ИСПОЛЬЗОВАННЫХ\s+ИСТОЧНИКОВ"
        r"|СПИСОК\s+ЛИТЕРАТУРЫ"
        r"|REFERENCES)",
    ),
)

_HEADING_PATTERNS: tuple[tuple[SectionKind, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(_LEADING_ORDINAL + form + _PUNCT_TAIL, re.IGNORECASE | re.UNICODE))
    for kind, form in _SIMPLE_HEADING_FORMS
) + (
    (
        SectionKind.CHAPTER,
        re.compile(
            _LEADING_ORDINAL
            + r"(?:РОЗДІЛ|ГЛАВА|ЧАСТИНА|ЧАСТЬ|CHAPTER)\s+"
            + _CHAPTER_NUMBER
            + r"\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        SectionKind.APPENDIX,
        re.compile(
            _LEADING_ORDINAL + r"(?:ДОДАТКИ|ДОДАТОК|ПРИЛОЖЕНИЯ|ПРИЛОЖЕНИЕ|APPENDIX)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
)

# Ліберальніша прикмета «рядок починає заголовок»: використовується тільки
# для розриву блоків (§5.2, п.4), а не для класифікації розділу. Хибний
# розрив нешкідливий, злиплий заголовок — шкідливий.
_HEADING_HINT_RE = re.compile(
    _LEADING_ORDINAL
    + r"(?:ЗМІСТ|СОДЕРЖАНИЕ|CONTENTS|АНОТАЦІЯ|АННОТАЦИЯ|ABSTRACT|РЕФЕРАТ"
    r"|ВСТУП|ВВЕДЕННЯ|ВВЕДЕНИЕ|ВИСНОВКИ|ВЫВОДЫ|ЗАКЛЮЧЕНИЕ"
    r"|РОЗДІЛ|ГЛАВА|ЧАСТИНА|ЧАСТЬ|CHAPTER"
    r"|СПИСОК\s+ВИКОРИСТАНИХ|СПИСОК\s+ВИКОРИСТАНОЇ|СПИСОК\s+ЛІТЕРАТУРИ"
    r"|БІБЛІОГРАФІЯ|СПИСОК\s+ИСПОЛЬЗОВАННЫХ|СПИСОК\s+ЛИТЕРАТУРЫ|REFERENCES"
    r"|ДОДАТКИ|ДОДАТОК|ПРИЛОЖЕНИЯ|ПРИЛОЖЕНИЕ|APPENDIX)\b",
    re.IGNORECASE | re.UNICODE,
)

# Римські цифри: кириличні двійники зводяться до латиниці.
_ROMAN_LOOKALIKES = str.maketrans({"І": "I", "Х": "X", "С": "C", "М": "M"})
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Цитати (§6.2): парні «…», „…“ та симетричні "…".
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (("«", "»"), ("„", "“"))
_SYMMETRIC_QUOTE = '"'

# §5.3: числа в колонтитулі зводяться до placeholder перед порівнянням.
_DIGITS_RE = re.compile(r"\d+")
# Блок із самих лише цифр: колонка номерів сторінок у двоколонковому ЗМІСТі.
_DIGITS_ONLY_RE = re.compile(r"[\d\s.,–—-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_NUMBER_PLACEHOLDER = "#"

_RANK = {zone: index for index, zone in enumerate(_ZONE_PRIORITY)}


# ---------------------------------------------------------------------------
# Внутрішні структури (публічними не стають ніколи)
# ---------------------------------------------------------------------------


@dataclass
class _Line:
    """Один рядок PDF з геометрією; за межі модуля не виходить (§5.1)."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    physical_page: int
    column: int = 0
    is_header_footer: bool = False
    heading_hint: bool = False


@dataclass
class _Page:
    physical_page: int
    width: float
    height: float
    lines: list[_Line]
    raster_ratio: float


@dataclass
class _RawBlock:
    physical_page: int
    lines: list[_Line]
    raw_text: str
    is_header_footer: bool
    is_footnote: bool
    # Блок цілком лежить у верхній або нижній смузі `_HEADER_FOOTER_BAND`:
    # там живуть номери сторінок, навіть якщо повторюваним колонтитулом
    # вони не стали (§5.3, поріг 60 % придатних аркушів).
    in_margin_band: bool = False
    block_index: int = -1
    block_id: str = ""


@dataclass
class _Heading:
    kind: SectionKind
    ordinal: int | None
    text: str
    confidence: Confidence


@dataclass
class _Segment:
    kind: SectionKind
    ordinal: int | None
    heading: str
    confidence: Confidence
    members: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Публічний вхід
# ---------------------------------------------------------------------------


def parse_search_document(
    pdf_bytes: bytes, *, overrides: tuple[SectionOverride, ...] = ()
) -> SearchDocument:
    """
    Будує `SearchDocument` з байтів PDF (§5.1–§5.4, §6.1–§6.2).

    `overrides` не правлять готовий результат «на місці»: розбір щоразу
    проходить один і той самий шлях, а виправлення впливає лише на етап
    класифікації заголовків, після чого карта розділів, зони, донори й
    охоплення рахуються з нуля (§6.1).

    Піднімає `NoTextLayerError`, якщо у PDF немає жодного текстового
    символу, і `ValueError`, якщо override посилається на невідомий
    `heading_block_id`.
    """
    document_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    pages = _extract_pages(pdf_bytes)
    n_pages = len(pages)

    if n_pages == 0 or not any(line.text for page in pages for line in page.lines):
        raise NoTextLayerError(
            "У PDF немає текстового шару. Розпізнавання сканів (OCR) не "
            "виконується — завантажте PDF із текстовим шаром."
        )

    _mark_header_footer(pages)
    raw_blocks = _build_raw_blocks(pages)
    _assign_block_ids(raw_blocks)
    _validate_overrides(raw_blocks, overrides)

    headings = _detect_headings(raw_blocks, overrides)
    biblio_confidence = _resolve_bibliography_boundary(raw_blocks, headings)
    segments = _build_segments(raw_blocks, headings)

    blocks = _build_search_blocks(raw_blocks, segments, pages)
    sections = _build_sections(blocks, segments)

    page_stats = _page_statistics(blocks, pages)
    page_infos = tuple(
        _build_page_info(page, page_stats[page.physical_page]) for page in pages
    )
    page_has_text = {
        page.physical_page: page_stats[page.physical_page]["content_chars"] > 0
        for page in pages
    }
    page_raster = {page.physical_page: page.raster_ratio for page in pages}

    extents = _section_extents(sections, n_pages)
    coverage = {
        section.section_id: _coverage_pages(
            section, extents.get(section.section_id), blocks, page_has_text, page_raster
        )
        for section in sections
    }
    sections = tuple(
        _with_coverage(
            section,
            extents.get(section.section_id),
            *coverage[section.section_id],
        )
        for section in sections
    )

    expected, extractable = _document_coverage(sections, coverage)
    coverage_ratio = extractable / expected if expected else 0.0

    heading_block_ids = frozenset(
        raw_blocks[index].block_id for index in headings if index < len(raw_blocks)
    )
    sentences = _build_sentence_donors(blocks, document_sha256, heading_block_ids)

    document = SearchDocument(
        document_sha256=document_sha256,
        parser_version=PARSER_VERSION,
        n_pages=n_pages,
        pages=page_infos,
        expected_body_pages=expected,
        extractable_body_pages=extractable,
        coverage_ratio=coverage_ratio,
        blocks=tuple(blocks),
        sections=sections,
        sentences=sentences,
        bibliography=(),
        citations=(),
        body_biblio_confidence=biblio_confidence,
        applied_overrides=tuple(overrides),
    )

    # Крок 6 (§12.5, §12.6): бібліографія і згадки джерел будуються як чисті
    # функції від уже готового документа, тому підключаються тут, наприкінці.
    entries = build_bibliography(document)
    return replace(
        document, bibliography=entries, citations=build_citations(document, entries)
    )


# ---------------------------------------------------------------------------
# §5.1 — читання сторінок
# ---------------------------------------------------------------------------


def _extract_pages(pdf_bytes: bytes) -> list[_Page]:
    import fitz

    pages: list[_Page] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            rect = page.rect
            lines = _extract_lines(page, page_index + 1)
            pages.append(
                _Page(
                    physical_page=page_index + 1,
                    width=float(rect.width),
                    height=float(rect.height),
                    lines=lines,
                    raster_ratio=_raster_ratio(page),
                )
            )
    return pages


def _extract_lines(page, physical_page: int) -> list[_Line]:
    text_dict = page.get_text("dict", sort=True)
    lines: list[_Line] = []
    for raw_block in text_dict.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        for raw_line in raw_block.get("lines", []):
            spans = [s for s in raw_line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            bbox = raw_line.get("bbox") or _spans_bbox(spans)
            lines.append(
                _Line(
                    text=text,
                    x0=float(bbox[0]),
                    y0=float(bbox[1]),
                    x1=float(bbox[2]),
                    y1=float(bbox[3]),
                    size=_dominant_size(spans),
                    bold=all(_span_is_bold(s) for s in spans),
                    physical_page=physical_page,
                    heading_hint=_is_heading_hint(text),
                )
            )
    return lines


def _is_heading_hint(text: str) -> bool:
    """
    Чи схожий рядок на початок заголовка — тільки для розриву блоків
    (§5.2, п.4), не для класифікації розділу.

    Рядок, що починається з малої літери, прикмети не дає: заголовки
    дисертацій набирають великими, а «…слухання. висновки суду про вину»
    посеред абзацу інакше розриває абзац і саме слово «висновки» стає
    заголовком розділу. Хибний розрив тут не нешкідливий.
    """
    if not text or text[0].islower():
        return False
    # Розрядка «В С Т У П» — теж заголовок, і саме він має розривати блок.
    return bool(_HEADING_HINT_RE.match(_collapse_letter_spacing(text)))


def _spans_bbox(spans: list[dict]) -> tuple[float, float, float, float]:
    boxes = [s.get("bbox", (0.0, 0.0, 0.0, 0.0)) for s in spans]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _dominant_size(spans: list[dict]) -> float:
    """Кегль рядка — той, яким набрано найбільше символів (детерміновано)."""
    weights: dict[float, int] = {}
    for span in spans:
        size = round(float(span.get("size", 0.0)), _FONT_SIZE_ROUND_DIGITS)
        weights[size] = weights.get(size, 0) + len(span.get("text", ""))
    if not weights:
        return 0.0
    return max(sorted(weights), key=lambda size: weights[size])


def _span_is_bold(span: dict) -> bool:
    if int(span.get("flags", 0)) & 16:
        return True
    font = str(span.get("font", "")).lower()
    return "bold" in font or "black" in font or "heavy" in font


def _raster_ratio(page) -> float:
    """Частка площі аркуша, вкрита розтровими зображеннями (§5.4)."""
    rect = page.rect
    page_area = float(rect.width) * float(rect.height)
    if page_area <= 0:
        return 0.0
    boxes: list[tuple[float, float, float, float]] = []
    try:
        images = page.get_images(full=True)
    except Exception:  # pragma: no cover - захист від пошкоджених PDF
        return 0.0
    for image in images:
        xref = image[0]
        try:
            image_rects = page.get_image_rects(xref)
        except Exception:  # pragma: no cover - захист від пошкоджених PDF
            continue
        for image_rect in image_rects:
            x0 = max(float(image_rect.x0), float(rect.x0))
            y0 = max(float(image_rect.y0), float(rect.y0))
            x1 = min(float(image_rect.x1), float(rect.x1))
            y1 = min(float(image_rect.y1), float(rect.y1))
            if x1 > x0 and y1 > y0:
                boxes.append((x0, y0, x1, y1))
    return min(1.0, _union_area(boxes) / page_area)


def _union_area(boxes: list[tuple[float, float, float, float]]) -> float:
    """Площа об'єднання прямокутників (стиснення координат, без наближень)."""
    if not boxes:
        return 0.0
    xs = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
    total = 0.0
    for left, right in zip(xs, xs[1:]):
        width = right - left
        if width <= 0:
            continue
        intervals = sorted(
            (b[1], b[3]) for b in boxes if b[0] <= left and b[2] >= right
        )
        covered = 0.0
        cur_start: float | None = None
        cur_end: float | None = None
        for y0, y1 in intervals:
            if cur_end is None:
                cur_start, cur_end = y0, y1
            elif y0 > cur_end:
                covered += cur_end - (cur_start or 0.0)
                cur_start, cur_end = y0, y1
            else:
                cur_end = max(cur_end, y1)
        if cur_end is not None:
            covered += cur_end - (cur_start or 0.0)
        total += width * covered
    return total


# ---------------------------------------------------------------------------
# §5.3 — повторювані колонтитули
# ---------------------------------------------------------------------------


def _header_footer_key(text: str) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return _DIGITS_RE.sub(_NUMBER_PLACEHOLDER, collapsed)


def _mark_header_footer(pages: list[_Page]) -> None:
    """
    Позначає рядки у верхній та нижній смугах, що повторюються мінімум на
    `_HEADER_FOOTER_MIN_PAGE_RATIO` придатних сторінок (§5.3).

    «Придатна сторінка» — сторінка, на якій узагалі є текст: на порожніх
    аркушах гібридного PDF колонтитула не буває, і вони не мають знижувати
    частку. Якщо придатних сторінок менше за `_HEADER_FOOTER_MIN_PAGES`,
    колонтитули не шукаються взагалі.
    """
    eligible = [page for page in pages if page.lines]
    if len(eligible) < _HEADER_FOOTER_MIN_PAGES:
        return

    band_lines: dict[str, set[int]] = {}
    for page in eligible:
        top = page.height * _HEADER_FOOTER_BAND
        bottom = page.height * (1.0 - _HEADER_FOOTER_BAND)
        for line in page.lines:
            if line.y1 <= top or line.y0 >= bottom:
                band_lines.setdefault(_header_footer_key(line.text), set()).add(
                    page.physical_page
                )

    threshold = len(eligible) * _HEADER_FOOTER_MIN_PAGE_RATIO
    repeated = {key for key, pgs in band_lines.items() if len(pgs) >= threshold}
    if not repeated:
        return

    for page in eligible:
        top = page.height * _HEADER_FOOTER_BAND
        bottom = page.height * (1.0 - _HEADER_FOOTER_BAND)
        for line in page.lines:
            if (line.y1 <= top or line.y0 >= bottom) and _header_footer_key(
                line.text
            ) in repeated:
                line.is_header_footer = True


# ---------------------------------------------------------------------------
# §5.2 — колонки і збирання рядків у блоки
# ---------------------------------------------------------------------------


def _assign_columns(page: _Page) -> None:
    """
    Дві колонки фіксуються, якщо стійкі кластери `x0` розділені мінімум на
    `_COLUMN_GAP_FACTOR ×` ширини аркуша і мають вертикальне перекриття
    (§5.2, п.5). Інакше вся сторінка — одна колонка.
    """
    body = [line for line in page.lines if not line.is_header_footer]
    if len(body) < 2:
        return
    xs = sorted({round(line.x0, _COORD_ROUND_DIGITS) for line in body})
    gap_limit = page.width * _COLUMN_GAP_FACTOR
    splits = [
        (left + right) / 2.0
        for left, right in zip(xs, xs[1:])
        if right - left >= gap_limit
    ]
    if len(splits) != 1:
        return
    split = splits[0]
    left_lines = [line for line in body if line.x0 < split]
    right_lines = [line for line in body if line.x0 >= split]
    if not left_lines or not right_lines:
        return
    left_top, left_bottom = min(l.y0 for l in left_lines), max(l.y1 for l in left_lines)
    right_top = min(l.y0 for l in right_lines)
    right_bottom = max(l.y1 for l in right_lines)
    if min(left_bottom, right_bottom) <= max(left_top, right_top):
        return  # немає вертикального перекриття — це не колонки
    for line in right_lines:
        line.column = 1


def _page_median_line_height(page: _Page) -> float:
    heights = [line.y1 - line.y0 for line in page.lines if line.y1 > line.y0]
    return float(median(heights)) if heights else 1.0


def _page_median_font_size(page: _Page) -> float:
    sizes = [line.size for line in page.lines if line.size > 0]
    return float(median(sizes)) if sizes else 0.0


def _should_break(prev: _Line, cur: _Line, page: _Page, median_height: float) -> bool:
    """§5.2, п.4: чи починає `cur` новий блок після `prev`."""
    if prev.column != cur.column:
        return True
    if prev.is_header_footer or cur.is_header_footer:
        return True
    if prev.heading_hint or cur.heading_hint:
        return True
    if _is_visual_heading(prev) or _is_visual_heading(cur):
        return True
    if cur.y0 - prev.y1 > _LINE_MERGE_GAP_FACTOR * median_height:
        return True
    if abs(cur.x0 - prev.x0) > _LINE_MERGE_LEFT_SHIFT_FACTOR * page.width:
        return True
    smaller = min(prev.size, cur.size)
    if smaller > 0 and abs(cur.size - prev.size) / smaller > _FONT_SIZE_BREAK_RATIO:
        return True
    return False


def _is_visual_heading(line: _Line) -> bool:
    """
    Прикмета жирного заголовка (§5.2, п.4): увесь рядок набрано жирним і він
    не довший за `_MAX_HEADING_WORDS`. Ознака центрування свідомо НЕ
    реалізована: пакет кроку не дає для неї жодного числа, а вигадувати
    поріг заборонено (CLAUDE.md, правило №8).
    """
    return line.bold and len(line.text.split()) <= _MAX_HEADING_WORDS


def _same_baseline(left: _Line, right: _Line) -> bool:
    """
    Чи це шматки одного візуального рядка. Критерій суто геометричний і без
    вигаданих порогів: вертикальні центри обох прямокутників лежать усередині
    чужого прямокутника. Сусідні рядки абзацу цього не дають.
    """
    left_center = (left.y0 + left.y1) / 2.0
    right_center = (right.y0 + right.y1) / 2.0
    return (
        left.y0 <= right_center <= left.y1 and right.y0 <= left_center <= right.y1
    )


def _merge_baseline_pieces(lines: list[_Line]) -> list[_Line]:
    """
    Склеює шматки одного візуального рядка в один `_Line` (§5.2, п.1–2).

    PyMuPDF ділить рядок на кілька записів `lines`, коли між словами широкий
    пробіл (виключка) або різко змінюється span. Без склейки шматок посеред
    рядка виглядає як початок рядка, і слово «Висновки» всередині речення
    стає заголовком розділу.
    """
    ordered = sorted(
        lines,
        key=lambda l: (l.column, round(l.y0, _COORD_ROUND_DIGITS), l.x0),
    )
    merged: list[_Line] = []
    for line in ordered:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.column == line.column
            and _same_baseline(previous, line)
        ):
            # Навіть у sort-режимі PyMuPDF може віддати короткий лівий гліф
            # (найчастіше маркер списку) після основного span через малу
            # різницю y0. Частини однієї базової лінії йдуть за геометрією,
            # інакше ``‒ вперше`` стає ``вперше … ‒``, а якір Ctrl+F зникає.
            if line.x0 < previous.x0:
                previous.text = f"{line.text} {previous.text}"
            else:
                previous.text = f"{previous.text} {line.text}"
            previous.x0 = min(previous.x0, line.x0)
            previous.x1 = max(previous.x1, line.x1)
            previous.y0 = min(previous.y0, line.y0)
            previous.y1 = max(previous.y1, line.y1)
            previous.size = max(previous.size, line.size)
            previous.bold = previous.bold and line.bold
            previous.is_header_footer = previous.is_header_footer or line.is_header_footer
            continue
        merged.append(
            _Line(
                text=line.text,
                x0=line.x0,
                y0=line.y0,
                x1=line.x1,
                y1=line.y1,
                size=line.size,
                bold=line.bold,
                physical_page=line.physical_page,
                column=line.column,
                is_header_footer=line.is_header_footer,
                heading_hint=line.heading_hint,
            )
        )
    for line in merged:
        line.heading_hint = _is_heading_hint(line.text)
    return merged


def _build_raw_blocks(pages: list[_Page]) -> list[_RawBlock]:
    """Збирає рядки в блоки посторінково: через межу аркуша не склеюємо (§5.2, п.7)."""
    blocks: list[_RawBlock] = []
    for page in pages:
        _assign_columns(page)
        median_height = _page_median_line_height(page)
        median_size = _page_median_font_size(page)
        ordered = _merge_baseline_pieces(page.lines)
        group: list[_Line] = []
        for line in ordered:
            if group and _should_break(group[-1], line, page, median_height):
                blocks.append(_make_raw_block(group, page, median_size))
                group = []
            group.append(line)
        if group:
            blocks.append(_make_raw_block(group, page, median_size))
    return blocks


def _make_raw_block(lines: list[_Line], page: _Page, median_size: float) -> _RawBlock:
    return _RawBlock(
        physical_page=page.physical_page,
        lines=list(lines),
        raw_text="\n".join(line.text for line in lines),
        is_header_footer=any(line.is_header_footer for line in lines),
        is_footnote=_is_footnote(lines, page, median_size),
        in_margin_band=_in_margin_band(lines, page),
    )


def _in_margin_band(lines: list[_Line], page: _Page) -> bool:
    top = page.height * _HEADER_FOOTER_BAND
    bottom = page.height * (1.0 - _HEADER_FOOTER_BAND)
    return all(line.y1 <= top or line.y0 >= bottom for line in lines)


def _is_footnote(lines: list[_Line], page: _Page, median_size: float) -> bool:
    """§6.2: нижня чверть аркуша плюс кегль не більший за 0,9 медіанного."""
    if any(line.is_header_footer for line in lines):
        return False
    if median_size <= 0:
        return False
    band_top = page.height * (1.0 - _FOOTNOTE_BAND)
    if min(line.y0 for line in lines) < band_top:
        return False
    block_size = max(line.size for line in lines)
    return block_size <= median_size * _FOOTNOTE_FONT_RATIO


def _assign_block_ids(raw_blocks: list[_RawBlock]) -> None:
    for index, block in enumerate(raw_blocks):
        block.block_index = index
        block.block_id = f"blk-{index:05d}"


# ---------------------------------------------------------------------------
# §6.1 — заголовки і карта розділів
# ---------------------------------------------------------------------------


def _roman_to_int(token: str) -> int | None:
    text = token.translate(_ROMAN_LOOKALIKES).upper()
    if not text or any(ch not in _ROMAN_VALUES for ch in text):
        return None
    total = 0
    previous = 0
    for ch in reversed(text):
        value = _ROMAN_VALUES[ch]
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total or None


def _collapse_letter_spacing(line: str) -> str:
    """
    Зводить розрядку «В С Т У П» до «ВСТУП» перед звіркою з шаблоном.

    Розрядка — типове оформлення заголовків у старих дисертаціях, і в
    текстовому шарі вона лишається окремими літерами з пробілами.
    Склеюються лише серії з `_LETTER_SPACING_MIN_RUN` і більше однолітерних
    токенів поспіль: так «РОЗДІЛ I» (один однолітерний токен — римський
    номер) лишається недоторканим.
    """
    tokens = line.split()
    if len(tokens) < _LETTER_SPACING_MIN_RUN:
        return line
    result: list[str] = []
    run: list[str] = []
    for token in tokens:
        if len(token) == 1 and _LETTER_RE.match(token):
            run.append(token)
            continue
        if len(run) >= _LETTER_SPACING_MIN_RUN:
            result.append("".join(run))
        else:
            result.extend(run)
        run = []
        result.append(token)
    if len(run) >= _LETTER_SPACING_MIN_RUN:
        result.append("".join(run))
    else:
        result.extend(run)
    return " ".join(result)


def _classify_heading_line(line: str) -> tuple[SectionKind, int | None, int] | None:
    """
    Повертає `(kind, ordinal, кінець ключової форми)` або `None`.

    Збіг шукається від початку рядка; для простих форм хвостом може бути
    лише пунктуація, для `CHAPTER` і `APPENDIX` — назва розділу.
    `CHAPTER` без номера заголовком не вважається.
    """
    stripped = line.strip()
    if not stripped:
        return None
    for kind, pattern in _HEADING_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        ordinal: int | None = None
        if kind == SectionKind.CHAPTER:
            token = match.group(1)
            ordinal = int(token) if token.isdigit() else _roman_to_int(token)
        return kind, ordinal, match.end()
    return None


def _toc_entry_strength(block: _RawBlock, keyform_end: int = 0) -> int:
    """
    §6.1, відсічення записів ЗМІСТу, пункти 1 і 2. Повертає:

    * 2 — сильна ознака запису: точкові лідери, табуляція,
      `parser.types.is_toc_entry` або блок із самих лише цифр (колонка
      номерів сторінок праворуч від назв — типова двоколонкова верстка
      ЗМІСТу);
    * 1 — слабка ознака: рядок закінчується числом (номер сторінки);
    * 0 — ознак запису немає.

    Перевіряється **весь блок**, а не лише перший рядок: у реальних
    дисертаціях довгий запис ЗМІСТу переноситься на два-три рядки, і
    крапкові лідери з номером сторінки стоять в останньому з них, а
    ключова форма («РОЗДІЛ 1.») — у першому.
    """
    # Розрядка знімається до перевірки: «Р О З Д І Л   1» інакше виглядає
    # як запис ЗМІСТу (два пробіли перед числом — ознака `is_toc_entry`).
    lines = [_collapse_letter_spacing(line.text.strip()) for line in block.lines]
    if not lines:
        return 0
    if all(_DIGITS_ONLY_RE.fullmatch(line) for line in lines):
        # Номер сторінки у смузі полів — не запис ЗМІСТу, навіть якщо
        # повторюваним колонтитулом він не став (поріг 60 % §5.3).
        return 0 if block.in_margin_band else 2
    if any(_DOT_LEADERS_RE.search(line) for line in lines):
        return 2
    if any(is_toc_entry(line) for line in lines):
        return 2
    if len(lines) > 1:
        return 1 if _TRAILING_NUMBER_RE.search(lines[-1]) else 0
    tail = lines[0][keyform_end:]
    return 1 if _TRAILING_NUMBER_RE.search(tail) else 0


def _is_toc_like(block: _RawBlock, keyform_end: int) -> bool:
    """Пункти 1 і 2 §6.1 як булеве рішення «це запис ЗМІСТу»."""
    return _toc_entry_strength(block, keyform_end) > 0


def _validate_overrides(
    raw_blocks: list[_RawBlock], overrides: tuple[SectionOverride, ...]
) -> None:
    known = {block.block_id for block in raw_blocks}
    unknown = [o.heading_block_id for o in overrides if o.heading_block_id not in known]
    if unknown:
        raise ValueError(
            "Невідомий heading_block_id у SectionOverride: " + ", ".join(unknown)
        )


def _detect_headings(
    raw_blocks: list[_RawBlock], overrides: tuple[SectionOverride, ...]
) -> dict[int, _Heading]:
    excluded = {
        o.heading_block_id
        for o in overrides
        if o.action == SectionOverrideAction.EXCLUDE_HEADING
    }
    forced = {
        o.heading_block_id: o.section_kind
        for o in overrides
        if o.action == SectionOverrideAction.SET_KIND and o.section_kind is not None
    }

    candidates: dict[int, _Heading] = {}
    forced_indices: set[int] = set()
    for index, block in enumerate(raw_blocks):
        if block.block_id in excluded:
            continue
        first_line = _collapse_letter_spacing(
            block.lines[0].text if block.lines else ""
        )
        if block.block_id in forced:
            kind = forced[block.block_id]
            classified = _classify_heading_line(first_line)
            ordinal = (
                classified[1]
                if classified is not None and kind == SectionKind.CHAPTER
                else None
            )
            candidates[index] = _Heading(kind, ordinal, first_line.strip(), Confidence.HIGH)
            forced_indices.add(index)
            continue
        if block.is_header_footer:
            continue
        classified = _classify_heading_line(first_line)
        if classified is None:
            continue
        kind, ordinal, keyform_end = classified
        # Рішення оркестратора, крок 5: заголовок довший за
        # `_MAX_HEADING_WORDS` слів заголовком не вважається. Рахується
        # весь блок, а не перший рядок: інакше заголовком стає кожен абзац
        # тіла, що починається зі слова «Розділ», «Глава» чи «Частина»
        # («Розділ ІІ Конституції України встановлює такі обов'язки: …»).
        if len(block.raw_text.split()) > _MAX_HEADING_WORDS:
            continue
        if _is_toc_like(block, keyform_end):
            continue
        candidates[index] = _Heading(kind, ordinal, first_line.strip(), Confidence.HIGH)

    return _drop_toc_region(raw_blocks, candidates, forced_indices)


def _drop_toc_region(
    raw_blocks: list[_RawBlock],
    candidates: dict[int, _Heading],
    forced_indices: set[int],
) -> dict[int, _Heading]:
    """
    §6.1, відсічення записів ЗМІСТу, пункти 3 і 4.

    П.3: блок усередині вже розміченого розділу `TOC` заголовком не стає.
    П.4: якщо та сама форма трапляється далі в документі окремим блоком,
    заголовком вважається **останнє** входження, а раніші потрапляють у
    розділ `TOC`.

    Обидва пункти зводяться до однієї межі — де саме закінчується ЗМІСТ.
    Вона визначається так:

    1. аркуш заголовка `ЗМІСТ` належить області; кожен наступний аркуш
       поспіль додається, доки на ньому є **сильний** запис змісту
       (`_toc_entry_strength == 2`): лідери, табуляція, `is_toc_entry`
       або блок із самих цифр;
    2. область закінчується на останньому записі (сильному чи слабкому)
       в межах цих аркушів.

    Усі кандидати всередині області заголовками не стають, отже виграє
    пізніше входження в тілі роботи — це і є п.4.

    Чому не «останнє входження по всьому документу»: у справжніх
    дисертаціях «ВИСНОВКИ» чи «ВСТУП» трапляються ще раз у додатках і
    таблицях, і глобальне правило перенесло б змістовний розділ у додатки.
    Чому не «до першого блоку без ознак запису»: довгий запис змісту
    переноситься на кілька рядків, і його початок («РОЗДІЛ 2. НАЗВА»)
    ознак запису не має — лідери лишаються в наступному блоці, а в
    двоколонковій верстці номери сторінок узагалі стоять окремою
    колонкою. Чому слабка ознака не подовжує область на новий аркуш:
    рядок, що закінчується числом, трапляється і в тілі роботи, і одного
    такого блоку досить, щоб область поглинула всю роботу.
    """
    if not candidates:
        return {}

    strength = {
        index: _toc_entry_strength(block)
        for index, block in enumerate(raw_blocks)
        if not block.is_header_footer
    }

    dropped: set[int] = set()
    for start in sorted(candidates):
        if candidates[start].kind != SectionKind.TOC or start in dropped:
            continue
        strong_pages = {
            raw_blocks[index].physical_page
            for index, value in strength.items()
            if value == 2 and index > start
        }
        page = raw_blocks[start].physical_page
        pages = {page}
        while page + 1 in strong_pages:
            page += 1
            pages.add(page)
        last_entry = max(
            (
                index
                for index, value in strength.items()
                if value > 0 and index > start and raw_blocks[index].physical_page in pages
            ),
            default=-1,
        )
        if last_entry < 0:
            continue  # заголовок ЗМІСТу без жодного запису — області немає
        dropped.update(
            index
            for index in candidates
            if start < index <= last_entry and index not in forced_indices
        )

    dropped.update(_candidates_between_entries(raw_blocks, candidates, forced_indices, strength))
    return {index: heading for index, heading in candidates.items() if index not in dropped}


def _candidates_between_entries(
    raw_blocks: list[_RawBlock],
    candidates: dict[int, _Heading],
    forced_indices: set[int],
    strength: dict[int, int],
) -> set[int]:
    """
    §6.1, п.3 для переліку без заголовка «ЗМІСТ».

    Трапляється, що зміст надрукований списком без власного заголовка. Тоді
    розділу `TOC` немає і область із `_drop_toc_region` не починається, а
    початок довгого запису («РОЗДІЛ 1 ТЕОРІЯ ТА ПРАКТИКА…») крапкових
    лідерів не має — вони лишилися в сусідньому блоці. Ознака така: і
    попередній, і наступний блок того самого аркуша — записи змісту.
    Всередині тіла роботи заголовок так не оточений.
    """
    order = [index for index in range(len(raw_blocks)) if not raw_blocks[index].is_header_footer]
    position = {index: place for place, index in enumerate(order)}
    dropped: set[int] = set()
    for index in candidates:
        if index in forced_indices or index not in position:
            continue
        place = position[index]
        if place == 0 or place + 1 >= len(order):
            continue
        previous, following = order[place - 1], order[place + 1]
        page = raw_blocks[index].physical_page
        if raw_blocks[previous].physical_page != page or raw_blocks[following].physical_page != page:
            continue
        if strength.get(previous, 0) > 0 and strength.get(following, 0) > 0:
            dropped.add(index)
    return dropped


def _resolve_bibliography_boundary(
    raw_blocks: list[_RawBlock], headings: dict[int, _Heading]
) -> Confidence:
    """
    §6.1: три результати визначення межі «тіло / бібліографія».

    HIGH — заголовок знайдено шаблоном; MEDIUM — межу дав
    `parser.bibliography.split_zones`; LOW — `split_zones` підняв
    `BibliographyNotFoundError`, розділ `BIBLIO` не створюється і питання
    лишається користувачеві (екран — крок 15).
    """
    if any(h.kind == SectionKind.BIBLIO for h in headings.values()):
        return Confidence.HIGH

    line_items: list[dict] = []
    owners: list[int] = []
    for index, block in enumerate(raw_blocks):
        if block.is_header_footer:
            continue
        for line in block.lines:
            line_items.append({"line": line.text, "page": block.physical_page})
            owners.append(index)
    if not line_items:
        return Confidence.LOW

    try:
        zones = split_zones(line_items)
    except BibliographyNotFoundError:
        return Confidence.LOW

    boundary = len(zones.body)
    if boundary >= len(owners):
        return Confidence.LOW
    block_index = owners[boundary]
    headings[block_index] = _Heading(
        SectionKind.BIBLIO,
        None,
        (zones.biblio_header_line or raw_blocks[block_index].lines[0].text).strip(),
        Confidence.MEDIUM,
    )
    return Confidence.MEDIUM


def _build_segments(
    raw_blocks: list[_RawBlock], headings: dict[int, _Heading]
) -> list[_Segment]:
    """
    Кожен блок належить рівно одному сегменту. Блоки **до першого
    заголовка** дають `TITLE` на аркушах 1–`_TITLE_MAX_PAGE` і `UNKNOWN`
    далі.

    Титульний аркуш визначено як «блоки перед першим розпізнаним
    заголовком». Якщо в документі не розпізнано жодного заголовка, такого
    «перед» не існує: тоді весь текст лишається `UNKNOWN` і не видає себе
    за титулку (§6.1: нерозпізнаний авторський фрагмент отримує `UNKNOWN`,
    але не зникає).
    """
    title_allowed = bool(headings)
    segments: list[_Segment] = []
    current: _Segment | None = None
    for index, block in enumerate(raw_blocks):
        heading = headings.get(index)
        if heading is not None:
            title_allowed = False
            current = _Segment(
                kind=heading.kind,
                ordinal=heading.ordinal,
                heading=heading.text,
                confidence=heading.confidence,
            )
            segments.append(current)
            current.members.append(index)
            continue
        preamble = _preamble_kind(block.physical_page, title_allowed)
        if current is None or (current.heading == "" and current.kind != preamble):
            current = _Segment(
                kind=preamble,
                ordinal=None,
                heading="",
                confidence=Confidence.LOW,
            )
            segments.append(current)
        current.members.append(index)
    return segments


def _preamble_kind(physical_page: int, title_allowed: bool) -> SectionKind:
    if title_allowed and physical_page <= _TITLE_MAX_PAGE:
        return SectionKind.TITLE
    return SectionKind.UNKNOWN


# ---------------------------------------------------------------------------
# §6.2 і §4.1 — зони інтервалами
# ---------------------------------------------------------------------------


def _quote_layers(text: str) -> list[tuple[int, int, TextZone, str]]:
    layers: list[tuple[int, int, TextZone, str]] = []
    for open_ch, close_ch in _QUOTE_PAIRS:
        cursor = 0
        while True:
            start = text.find(open_ch, cursor)
            if start < 0:
                break
            end = text.find(close_ch, start + 1)
            if end < 0:
                layers.append((start, len(text), TextZone.UNCERTAIN, "unclosed_quote"))
                break
            layers.append((start, end + 1, TextZone.QUOTED_TEXT, "paired_quotes"))
            cursor = end + 1
    positions = [i for i, ch in enumerate(text) if ch == _SYMMETRIC_QUOTE]
    for i in range(0, len(positions) - 1, 2):
        layers.append(
            (positions[i], positions[i + 1] + 1, TextZone.QUOTED_TEXT, "paired_quotes")
        )
    if len(positions) % 2 == 1:
        layers.append(
            (positions[-1], len(text), TextZone.UNCERTAIN, "unclosed_quote")
        )
    return layers


def _block_zone_spans(
    raw_block: _RawBlock, segment_kind: SectionKind
) -> tuple[ZoneSpan, ...]:
    text = raw_block.raw_text
    if not text:
        return ()

    layers: list[tuple[int, int, TextZone, str]] = [
        (0, len(text), TextZone.AUTHOR_TEXT, "default_author")
    ]
    if segment_kind == SectionKind.TOC:
        layers.append((0, len(text), TextZone.TOC, "toc_section"))
    if segment_kind == SectionKind.BIBLIO:
        layers.append((0, len(text), TextZone.BIBLIOGRAPHY, "biblio_section"))
    if raw_block.is_footnote:
        layers.append((0, len(text), TextZone.FOOTNOTE_TEXT, "footnote_geometry"))
    if raw_block.is_header_footer:
        layers.append((0, len(text), TextZone.HEADER_FOOTER, "repeated_running_title"))
    layers.extend(_quote_layers(text))

    return _resolve_zone_layers(len(text), layers)


def _resolve_zone_layers(
    length: int, layers: list[tuple[int, int, TextZone, str]]
) -> tuple[ZoneSpan, ...]:
    """§4.1: перетин інтервалів зводиться за `search.types.ZONE_PRIORITY`."""
    zones: list[TextZone | None] = [None] * length
    detectors: list[str] = [""] * length
    for start, end, zone, detector in layers:
        start = max(0, start)
        end = min(length, end)
        for i in range(start, end):
            current = zones[i]
            if current is None or _RANK[zone] < _RANK[current]:
                zones[i] = zone
                detectors[i] = detector

    spans: list[ZoneSpan] = []
    index = 0
    while index < length:
        zone = zones[index]
        detector = detectors[index]
        end = index + 1
        while end < length and zones[end] == zone and detectors[end] == detector:
            end += 1
        if zone is not None:
            spans.append(
                ZoneSpan(
                    raw_start=index,
                    raw_end=end,
                    zone=zone,
                    confidence=_zone_confidence(zone),
                    detector=detector,
                )
            )
        index = end
    return tuple(spans)


def _zone_confidence(zone: TextZone) -> Confidence:
    if zone == TextZone.UNCERTAIN:
        return Confidence.LOW
    if zone == TextZone.AUTHOR_TEXT:
        return Confidence.MEDIUM
    return Confidence.HIGH


# ---------------------------------------------------------------------------
# Побудова SearchBlock / SectionInfo
# ---------------------------------------------------------------------------


def _build_search_blocks(
    raw_blocks: list[_RawBlock], segments: list[_Segment], pages: list[_Page]
) -> list[SearchBlock]:
    section_of: dict[int, tuple[str, _Segment]] = {}
    for seg_index, segment in enumerate(segments):
        section_id = f"sec-{seg_index:03d}"
        for member in segment.members:
            section_of[member] = (section_id, segment)

    blocks: list[SearchBlock] = []
    for index, raw_block in enumerate(raw_blocks):
        section_id, segment = section_of[index]
        raw_text = raw_block.raw_text
        normalized = normalize_text(raw_text)
        blocks.append(
            SearchBlock(
                block_id=raw_block.block_id,
                raw_text=raw_text,
                normalized=normalized,
                tokens=tokenize(raw_text, normalized),
                section_id=section_id,
                heading_path=(segment.heading,) if segment.heading else (),
                physical_page=raw_block.physical_page,
                block_index=raw_block.block_index,
                zone_spans=_block_zone_spans(raw_block, segment.kind),
            )
        )
    return blocks


def _author_spans(block: SearchBlock) -> tuple[ZoneSpan, ...]:
    return tuple(s for s in block.zone_spans if s.zone == TextZone.AUTHOR_TEXT)


def _token_in_spans(token: SearchToken, spans: tuple[ZoneSpan, ...]) -> bool:
    return any(s.raw_start <= token.raw_start and token.raw_end <= s.raw_end for s in spans)


def _author_word_count(block: SearchBlock) -> int:
    spans = _author_spans(block)
    if not spans:
        return 0
    return sum(1 for token in block.tokens if token.is_word and _token_in_spans(token, spans))


def _build_sections(
    blocks: list[SearchBlock], segments: list[_Segment]
) -> tuple[SectionInfo, ...]:
    sections: list[SectionInfo] = []
    for seg_index, segment in enumerate(segments):
        section_id = f"sec-{seg_index:03d}"
        members = [blocks[i] for i in segment.members]
        sections.append(
            SectionInfo(
                section_id=section_id,
                kind=segment.kind,
                ordinal=segment.ordinal,
                heading=segment.heading,
                block_start=segment.members[0],
                block_end=segment.members[-1] + 1,
                physical_pages=tuple(sorted({b.physical_page for b in members})),
                author_words=sum(_author_word_count(b) for b in members),
                expected_body_pages=0,
                extractable_body_pages=0,
                coverage_ratio=0.0,
                confidence=segment.confidence,
            )
        )
    return tuple(sections)


# ---------------------------------------------------------------------------
# §5.4 — стани сторінок і охоплення
# ---------------------------------------------------------------------------


def _page_statistics(blocks: list[SearchBlock], pages: list[_Page]) -> dict[int, dict]:
    stats: dict[int, dict] = {
        page.physical_page: {
            "content_chars": 0,
            "letters": 0,
            "words": 0,
            "author_words": 0,
            "short_blocks": 0,
            "content_blocks": 0,
        }
        for page in pages
    }
    for block in blocks:
        entry = stats[block.physical_page]
        if any(s.zone == TextZone.HEADER_FOOTER for s in block.zone_spans):
            continue
        words = sum(1 for t in block.tokens if t.is_word)
        entry["content_chars"] += sum(1 for ch in block.raw_text if not ch.isspace())
        entry["letters"] += len(_LETTER_RE.findall(block.raw_text))
        entry["words"] += words
        entry["author_words"] += _author_word_count(block)
        entry["content_blocks"] += 1
        if words <= _SHORT_BLOCK_MAX_WORDS:
            entry["short_blocks"] += 1
    return stats


def _is_extractable(letters: int, words: int) -> bool:
    return letters >= _MIN_EXTRACTABLE_LETTERS or words >= _MIN_EXTRACTABLE_WORDS


def _build_page_info(page: _Page, stats: dict) -> PageInfo:
    content_chars = stats["content_chars"]
    raster = page.raster_ratio
    if content_chars <= 0:
        state = PageTextState.NO_TEXT
        reason = "Аркуш без жодного текстового символу після зняття колонтитулів."
    elif content_chars >= _TEXT_OK_MIN_CHARS:
        state = PageTextState.TEXT_OK
        reason = ""
    elif raster >= _SPARSE_RASTER_RATIO:
        state = PageTextState.EXPECTED_SPARSE
        reason = (
            f"Розтр укриває {raster:.0%} аркуша — текст тут не очікується."
        )
    elif (
        stats["content_blocks"] <= _SPARSE_MAX_SHORT_BLOCKS
        and stats["content_blocks"] == stats["short_blocks"]
    ):
        state = PageTextState.EXPECTED_SPARSE
        reason = (
            f"На аркуші лише {stats['content_blocks']} короткий(-і) блок(и) "
            "вигляду заголовок/підпис — текст тут не очікується."
        )
    else:
        state = PageTextState.LOW_TEXT
        reason = f"Лише {content_chars} змістовних символів на аркуші."
    return PageInfo(
        physical_page=page.physical_page,
        content_chars=content_chars,
        author_words=stats["author_words"],
        large_raster_ratio=raster,
        extractable=_is_extractable(stats["letters"], stats["words"]),
        state=state,
        reason=reason,
    )


def _section_extents(
    sections: tuple[SectionInfo, ...], n_pages: int
) -> dict[str, tuple[int, int]]:
    """
    Смуга аркушів розділу: від першого аркуша його блоків до аркуша перед
    початком наступного розділу, а для останнього розділу — до кінця
    документа.

    Саме смуга, а не «аркуші, де є блоки» (уточнення оркестратора до
    розділу «Охоплення» пакета кроку 5): аркуш без жодного блоку — чиста
    ілюстрація, растрова вклейка, порожня сторінка — належить тому
    розділові, всередину діапазону якого потрапив. Інакше
    `expected_body_pages` мовчки ховав би саме ті аркуші, заради яких
    охоплення й рахується (§5.4).

    Аркуш на межі двох розділів входить в обидва: кінець попереднього не
    менший за аркуш його останнього блоку.
    """
    starts = [
        section.physical_pages[0] if section.physical_pages else None
        for section in sections
    ]
    extents: dict[str, tuple[int, int]] = {}
    for index, section in enumerate(sections):
        if not section.physical_pages:
            continue
        start = section.physical_pages[0]
        next_start = next(
            (value for value in starts[index + 1 :] if value is not None), None
        )
        end = n_pages if next_start is None else max(next_start - 1, start)
        end = max(end, section.physical_pages[-1])
        extents[section.section_id] = (start, min(end, n_pages))
    return extents


def _coverage_pages(
    section: SectionInfo,
    extent: tuple[int, int] | None,
    blocks: list[SearchBlock],
    page_has_text: dict[int, bool],
    page_raster: dict[int, float],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """
    §5.4: `expected_body_pages` — аркуші в межах розділу, де є текст або
    розтр ≥ 50 %; `extractable_body_pages` — ті з них, де блоки розділу
    дають ≥ 20 літер Unicode або ≥ 5 словесних токенів.
    """
    if extent is None:
        return (), ()
    by_page: dict[int, list[SearchBlock]] = {}
    for block in blocks:
        if block.section_id == section.section_id:
            by_page.setdefault(block.physical_page, []).append(block)

    expected: list[int] = []
    extractable: list[int] = []
    for page in range(extent[0], extent[1] + 1):
        if not (page_has_text.get(page, False) or page_raster.get(page, 0.0) >= _SPARSE_RASTER_RATIO):
            continue
        expected.append(page)
        page_blocks = by_page.get(page, [])
        letters = sum(len(_LETTER_RE.findall(b.raw_text)) for b in page_blocks)
        words = sum(sum(1 for t in b.tokens if t.is_word) for b in page_blocks)
        if _is_extractable(letters, words):
            extractable.append(page)
    return tuple(expected), tuple(extractable)


def _with_coverage(
    section: SectionInfo,
    extent: tuple[int, int] | None,
    expected: tuple[int, ...],
    extractable: tuple[int, ...],
) -> SectionInfo:
    """
    §5.4: знаменник нуль ⇒ охоплення 0.0, без `ZeroDivisionError`.

    `physical_pages` тут стає всією смугою розділу, включно з аркушами без
    жодного блоку (уточнення оркестратора до розділу «Охоплення»).
    """
    ratio = len(extractable) / len(expected) if expected else 0.0
    physical_pages = (
        tuple(range(extent[0], extent[1] + 1)) if extent else section.physical_pages
    )
    return SectionInfo(
        section_id=section.section_id,
        kind=section.kind,
        ordinal=section.ordinal,
        heading=section.heading,
        block_start=section.block_start,
        block_end=section.block_end,
        physical_pages=physical_pages,
        author_words=section.author_words,
        expected_body_pages=len(expected),
        extractable_body_pages=len(extractable),
        coverage_ratio=ratio,
        confidence=section.confidence,
    )


def _document_coverage(
    sections: tuple[SectionInfo, ...],
    coverage: dict[str, tuple[tuple[int, ...], tuple[int, ...]]],
) -> tuple[int, int]:
    """
    §5.4: документне охоплення рахується по **об'єднанню** фізичних
    сторінок змістовних розділів, тому сума роздільних лічильників не
    зобов'язана дорівнювати документному: одна фізична сторінка може
    входити в охоплення двох розділів.
    """
    pages_expected: set[int] = set()
    pages_extractable: set[int] = set()
    for section in sections:
        if section.kind not in CONTENT_SECTION_KINDS:
            continue
        expected, extractable = coverage.get(section.section_id, ((), ()))
        pages_expected.update(expected)
        pages_extractable.update(extractable)
    return len(pages_expected), len(pages_extractable)


# ---------------------------------------------------------------------------
# §10.1 — донори речень
# ---------------------------------------------------------------------------


def _build_sentence_donors(
    blocks: list[SearchBlock],
    document_sha256: str,
    heading_block_ids: frozenset[str],
) -> tuple[SentenceDonor, ...]:
    """
    Донорами стають лише блоки, у яких є авторський текст. Залишок
    останнього `AUTHOR_TEXT`-блоку фізичної сторінки без термінальної
    пунктуації донором не стає (§10.1), але завершені речення того самого
    блоку зберігаються.

    Блок-заголовок розділу донором не стає: його текст — назва розділу, а
    не авторське речення (§6.1 тримає його як межу розділу, §10.1 будує
    донорів з речень). У лічильниках він при цьому лишається: зона
    `AUTHOR_TEXT`, слова входять у `SectionInfo.author_words`.

    Для правила «останній `AUTHOR_TEXT`-блок сторінки» заголовок
    враховується нарівні з іншими: якщо аркуш закінчується заголовком,
    попередній абзац межею сторінки не обірваний.
    """
    last_author_block: dict[int, str] = {}
    for block in blocks:
        if _author_spans(block):
            last_author_block[block.physical_page] = block.block_id

    donors: list[SentenceDonor] = []
    occurrence_counts: dict[tuple[int, str], int] = {}
    for block in blocks:
        if not block.raw_text or not _author_spans(block):
            continue
        if block.block_id in heading_block_ids:
            continue
        is_last = last_author_block.get(block.physical_page) == block.block_id
        spans = split_sentences_detailed(
            block.raw_text, is_last_author_block_on_page=is_last
        )
        ordinal = 0
        for span in spans:
            if span.is_page_boundary_fragment:
                continue
            raw_text = block.raw_text[span.start : span.end]
            sentence_normalized = normalize_text(raw_text)
            normalized_text = sentence_normalized.text
            author_word_count = sum(
                1 for t in tokenize(raw_text, sentence_normalized) if t.is_word
            )
            key = (block.physical_page, normalized_text)
            occurrence_index = occurrence_counts.get(key, 0)
            occurrence_counts[key] = occurrence_index + 1
            donor_id = hashlib.sha256(
                f"{document_sha256}|{block.physical_page}|{normalized_text}"
                f"|{occurrence_index}".encode("utf-8")
            ).hexdigest()
            donors.append(
                SentenceDonor(
                    donor_id=donor_id,
                    block_id=block.block_id,
                    section_id=block.section_id,
                    sentence_ordinal=ordinal,
                    occurrence_index=occurrence_index,
                    source=SourceSpan(
                        parts=(
                            RawSpan(
                                block.block_id,
                                block.physical_page,
                                span.start,
                                span.end,
                            ),
                        )
                    ),
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    author_word_count=author_word_count,
                )
            )
            ordinal += 1
    return tuple(donors)
