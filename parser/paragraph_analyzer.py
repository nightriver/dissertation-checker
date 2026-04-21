"""
paragraph_analyzer.py
Аналіз абзаців дисертації на наявність посилань (Paragraph-Level Citation Gaps).
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field

from parser.citations import _BRACKET_RE

# ---------------------------------------------------------------------------
# Константи
# ---------------------------------------------------------------------------

MIN_SUSPICIOUS_SENTENCES = 5
MIN_BLOCK_CHARS = 80

CHAPTER_HEADERS = [
    "РОЗДІЛ",
    "CHAPTER",
    "ГЛАВА",
    "ЧАСТИНА",
]

SKIP_SECTION_HEADERS = [
    "ВСТУП",
    "ЗМІСТ",
    "ЗМІСТ ДИСЕРТАЦІЇ",
    "АНОТАЦІЯ",
    "ABSTRACT",
    "СПИСОК ПУБЛІКАЦІЙ ЗДОБУВАЧА",
    "ПОДЯКИ",
    "ACKNOWLEDGEMENTS",
]

END_SECTION_HEADERS = [
    "ВИСНОВКИ",
    "ЗАГАЛЬНІ ВИСНОВКИ",
    "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
    "СПИСОК ЛІТЕРАТУРИ",
    "СПИСОК ВИКОРИСТАНОЇ ЛІТЕРАТУРИ",
    "ВИКОРИСТАНІ ДЖЕРЕЛА",
    "БІБЛІОГРАФІЯ",
    "БІБЛІОГРАФІЧНИЙ СПИСОК",
    "REFERENCES",
    "ДОДАТКИ",
    "ДОДАТОК",
]

# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def _is_section_trigger(line: str, headers: list[str], exact: bool = False) -> bool:
    """
    Строга перевірка: рядок є заголовком секції, а не словом у середині речення.

    exact=False (CHAPTER_HEADERS): startswith-порівняння.
    exact=True  (END_SECTION_HEADERS): точний збіг після нормалізації.
    Кінцева пунктуація стрипається перед порівнянням.
    """
    normalized = re.sub(r"\s+", " ", line.strip().upper())
    normalized = re.sub(r"[.,:;!?]+$", "", normalized)

    if exact:
        if not any(normalized == h for h in headers):
            return False
    else:
        if not any(normalized.startswith(h) for h in headers):
            return False

    clean = line.strip()
    is_short = len(clean) < 60 and len(clean.split()) <= 8
    # isupper() повертає True якщо рядок містить хоча б одну велику літеру
    # і не містить малих — підходить для суто uppercase-заголовків
    is_uppercase = clean.upper() == clean and any(c.isalpha() for c in clean)
    return is_short or is_uppercase


class ContentBoundsNotFoundError(Exception):
    pass


def extract_content_bounds(
    lines: list[dict],
    biblio_start_page: int | None,
) -> tuple[int, int]:
    """Знаходить індекси початку та кінця змістовних розділів."""
    content_start_idx = None
    content_end_idx = len(lines) - 1

    TOC_LINE_RE = re.compile(r'\.{3,}\s*\d*\s*$')

    for i, item in enumerate(lines):
        line = item["line"]

        # Пропускаємо рядки оглавлення
        if TOC_LINE_RE.search(line.strip()):
            continue

        # exact=True — точний збіг, щоб «Висновки до розділу 1» не зупинив аналіз
        if _is_section_trigger(line, END_SECTION_HEADERS, exact=True):
            # Захист від i=0: не допускаємо від'ємного індексу
            content_end_idx = max(i - 1, 0)
            break

        if _is_section_trigger(line, CHAPTER_HEADERS, exact=False):
            if content_start_idx is None:
                content_start_idx = i

    if content_start_idx is None:
        raise ContentBoundsNotFoundError(
            "Не вдалося знайти початок змістовних розділів (РОЗДІЛ 1 тощо)."
        )

    # Якщо biblio_start_page відомий і знайдений content_end_idx виявився
    # пізніше ніж початок бібліографії — беремо мінімум
    if biblio_start_page is not None:
        for j, item in enumerate(lines):
            if item.get("page") == biblio_start_page:
                if j < content_end_idx:
                    content_end_idx = max(j - 1, 0)
                break

    return content_start_idx, content_end_idx


# ---------------------------------------------------------------------------
# Підрахунок речень
# ---------------------------------------------------------------------------

_ABBR_RE = re.compile(
    r'\b(табл|таб|рис|див|стор|ст|с|п|ч|т|д|дод|вид|грн|млн|млрд|кг|км|га|ін|тис|проф|доц|акад)\.',
    re.IGNORECASE | re.UNICODE,
)

# Lookbehind без \b — фіксована довжина, стабільний у всіх версіях Python
# Ігноруємо крапку після одиночної великої літери (ініціали: В., А.)
_SENTENCE_END = re.compile(
    r'(?<![А-ЯІЇЄҐA-Z]\.)(?<=[.!?])\s+(?=[А-ЯІЇЄҐA-Z])'
)


def _count_sentences(text: str) -> int:
    """Підрахунок кількості речень у тексті з ігноруванням скорочень."""
    cleaned = _ABBR_RE.sub(lambda m: m.group(0).replace(".", "\x00"), text)
    return len(_SENTENCE_END.findall(cleaned)) + 1


# ---------------------------------------------------------------------------
# Структура абзацу
# ---------------------------------------------------------------------------

@dataclass
class ParagraphItem:
    text: str
    page: int | None
    sentence_count: int
    para_index: int = 0          # порядковий індекс у документі (для DOCX-сортування)
    context_heading: str | None = None


# ---------------------------------------------------------------------------
# Екстрактори PDF / DOCX
# ---------------------------------------------------------------------------

def _extract_paragraphs_pdf(
    pdf_bytes: bytes,
    start_page: int | None,
    end_page: int | None,
) -> list[ParagraphItem]:
    """Витягує абзаци з PDF у діапазоні сторінок [start_page, end_page]."""
    import fitz

    result = []
    idx = 0
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            if start_page and page_num < start_page:
                continue
            if end_page and page_num > end_page:
                break
            for block in page.get_text("blocks"):
                if block[6] != 0:          # не текстовий блок
                    continue
                text = block[4].replace("\n", " ").strip()
                if len(text) < MIN_BLOCK_CHARS:
                    continue
                if not re.search(r'[А-Яа-яІіЇїЄєҐґA-Za-z]', text):
                    continue
                result.append(ParagraphItem(
                    text=text,
                    page=page_num,
                    sentence_count=_count_sentences(text),
                    para_index=idx,
                ))
                idx += 1
    return result


def _extract_paragraphs_docx(
    file_bytes: bytes,
    content_start_idx: int | None,
    content_end_idx: int | None,
    all_lines: list[dict],
) -> list[ParagraphItem]:
    """
    Витягує абзаци з DOCX, фільтруючи за межами змістовних розділів.

    Оскільки DOCX не має номерів сторінок, ми порівнюємо текст параграфів
    зі списком рядків `all_lines`, щоб визначити, чи потрапляє параграф
    у зону [content_start_idx, content_end_idx].

    Алгоритм: будуємо множину рядків-заголовків з зони SKIP (до content_start)
    та зони END (після content_end), і відкидаємо параграфи, що туди входять.
    Оскільки текстова відповідність ненадійна, використовуємо простіший підхід:
    відстежуємо, чи пройшли ми перший заголовок РОЗДІЛ, і зупиняємося на
    першому END_SECTION_HEADERS.
    """
    import io
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    result = []
    last_heading: str | None = None
    in_content_zone = False  # True після першого РОЗДІЛ
    idx = 0

    TOC_LINE_RE = re.compile(r'\.{3,}\s*\d*\s*$')

    for para in doc.paragraphs:
        style_name = para.style.name if para.style else ""
        text = para.text.strip()

        # Відстежуємо заголовки для context_heading
        if "Heading" in style_name or "heading" in style_name:
            last_heading = text or last_heading

        # Визначаємо межі зони аналізу через текст заголовків
        # (аналогічно extract_content_bounds, але по параграфах DOCX)
        if not TOC_LINE_RE.search(text):
            if _is_section_trigger(text, END_SECTION_HEADERS, exact=True):
                break  # вийшли за межі змістовної зони
            if _is_section_trigger(text, CHAPTER_HEADERS, exact=False):
                in_content_zone = True

        if not in_content_zone:
            continue

        # Пропускаємо заголовки та короткі рядки
        if "Heading" in style_name or "heading" in style_name:
            continue
        if len(text) < MIN_BLOCK_CHARS:
            continue
        if not re.search(r'[А-Яа-яІіЇїЄєҐґA-Za-z]', text):
            continue
        if TOC_LINE_RE.search(text):
            continue

        result.append(ParagraphItem(
            text=text,
            page=None,
            sentence_count=_count_sentences(text),
            para_index=idx,
            context_heading=last_heading,
        ))
        idx += 1

    return result


def extract_paragraphs(
    file_bytes: bytes,
    filename: str,
    content_start_page: int | None,
    content_end_page: int | None,
    content_start_idx: int | None = None,
    content_end_idx: int | None = None,
    all_lines: list[dict] | None = None,
) -> list[ParagraphItem]:
    """Єдина точка входу для PDF і DOCX."""
    if filename.lower().endswith(".pdf"):
        return _extract_paragraphs_pdf(file_bytes, content_start_page, content_end_page)
    elif filename.lower().endswith(".docx"):
        return _extract_paragraphs_docx(
            file_bytes,
            content_start_idx,
            content_end_idx,
            all_lines or [],
        )
    return []


# ---------------------------------------------------------------------------
# Перевірка наявності посилань
# ---------------------------------------------------------------------------

def paragraph_has_citation(text: str) -> bool:
    return bool(_BRACKET_RE.search(text))


# ---------------------------------------------------------------------------
# Результат аналізу
# ---------------------------------------------------------------------------

@dataclass
class ParagraphGapResult:
    total_paragraphs: int
    cited_paragraphs: int
    clean_paragraphs: int
    clean_pct: float
    suspicious: list[dict]
    docx_mode: bool


# ---------------------------------------------------------------------------
# Головна функція
# ---------------------------------------------------------------------------

def analyze_paragraph_gaps(
    file_bytes: bytes,
    filename: str,
    lines: list[dict],
    biblio_start_page: int | None,
) -> ParagraphGapResult:
    content_start_idx, content_end_idx = extract_content_bounds(lines, biblio_start_page)

    content_start_page = lines[content_start_idx].get("page")
    content_end_page = lines[content_end_idx].get("page")

    paragraphs = extract_paragraphs(
        file_bytes,
        filename,
        content_start_page,
        content_end_page,
        content_start_idx=content_start_idx,
        content_end_idx=content_end_idx,
        all_lines=lines,
    )

    total = len(paragraphs)
    cited = 0
    clean = 0
    suspicious = []

    for p in paragraphs:
        if paragraph_has_citation(p.text):
            cited += 1
        else:
            clean += 1
            if p.sentence_count >= MIN_SUSPICIOUS_SENTENCES:
                suspicious.append({
                    "page": p.page,
                    "para_index": p.para_index,
                    "sentence_count": p.sentence_count,
                    "text": p.text,
                    "context_heading": p.context_heading,
                })

    clean_pct = clean / total * 100 if total else 0.0

    is_docx = filename.lower().endswith(".docx")

    # Для PDF сортуємо за номером сторінки, для DOCX — за порядком у документі
    if is_docx:
        suspicious_sorted = sorted(suspicious, key=lambda x: x["para_index"])
    else:
        suspicious_sorted = sorted(suspicious, key=lambda x: (x["page"] or 0))

    return ParagraphGapResult(
        total_paragraphs=total,
        cited_paragraphs=cited,
        clean_paragraphs=clean,
        clean_pct=clean_pct,
        suspicious=suspicious_sorted,
        docx_mode=is_docx,
    )
