"""
paragraph_analyzer.py
Аналіз абзаців дисертації на наявність посилань (Paragraph-Level Citation Gaps).
"""

from __future__ import annotations
import re
from dataclasses import dataclass

from parser.citations import BRACKET_RE, expand_bracket
from parser.types import LineItem, TOC_LEADER_RE as _TOC_LEADER_RE, is_toc_entry as _is_toc_entry

# ---------------------------------------------------------------------------
# Константи
# ---------------------------------------------------------------------------

MIN_SUSPICIOUS_SENTENCES = 5
MIN_BLOCK_CHARS = 80

# Типовий ЗМІСТ дисертації займає 1–3 сторінки ≈ 30–80 рядків;
# відстань між записом у ЗМІСТі та реальним заголовком РОЗДІЛУ
# зазвичай > 150 рядків (ВСТУП + кілька сторінок тексту).
# Якщо розрив між двома входженнями «РОЗДІЛ» перевищує цей поріг —
# вважаємо, що перше входження було у ЗМІСТі, а друге — реальний заголовок.
_MIN_GAP_AFTER_TOC = 100

CHAPTER_HEADERS = [
    "РОЗДІЛ",
    "CHAPTER",
    "ГЛАВА",
    "ЧАСТИНА",
]

# ВСТУП, ЗМІСТ, АНОТАЦІЯ тощо окремим списком НЕ перелічуються: зона аналізу
# починається з першого РОЗДІЛУ після останнього ВСТУПУ, тому вся передмова
# відсікається структурно. Раніше тут стояв SKIP_SECTION_HEADERS, на який
# ніхто не посилався, — обіцянка «вступ і зміст виключені» трималася тільки
# на ньому й насправді не виконувалася.

# Заголовок, що завершує змістовну частину: далі йде підсумок, а не аналіз.
CONTENT_END_HEADERS = [
    "ВИСНОВКИ",
    "ЗАГАЛЬНІ ВИСНОВКИ",
]

# Секції, які стоять уже ПІСЛЯ висновків. Рухаючись назад і натрапивши на
# таку секцію, ми лише звужуємо межу й шукаємо далі вгору — справжній кінець
# змістовної частини (ВИСНОВКИ) розташований вище.
#
# Без цього поділу DOCX ловив «СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ» (останній маркер у
# документі) і зупинявся на ньому, через що розділ ВИСНОВКІВ рахувався як
# змістовний текст. У PDF це не проявлялося лише тому, що там пошук заздалегідь
# обмежений сторінкою початку бібліографії.
POST_CONTENT_HEADERS = [
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

END_SECTION_HEADERS = CONTENT_END_HEADERS + POST_CONTENT_HEADERS

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
    # Захист: рядок без жодної літери (напр. "1.") не є заголовком
    is_uppercase = clean.upper() == clean and any(c.isalpha() for c in clean)
    return is_short or is_uppercase


class ContentBoundsNotFoundError(Exception):
    pass


def find_content_bounds_in_texts(
    texts: list[str],
    search_end_idx: int | None = None,
) -> tuple[int, int]:
    """
    Ядро пошуку меж змістовних розділів. Працює над простим списком рядків,
    тому однаково придатне і для рядків PDF, і для параграфів DOCX.

    Алгоритм — двонаправлений пошук:

    1. Верхня межа пошуку — search_end_idx (початок бібліографії) або кінець.
    2. Шукаємо ВИСНОВКИ рухаючись НАЗАД від цієї межі —
       це гарантує, що ми знайдемо справжні ВИСНОВКИ, а не рядок
       "Висновки" у ЗМІСТі (де він зустрічається набагато раніше).
    3. Шукаємо останній ВСТУП до знайдених ВИСНОВКІВ —
       реальний ВСТУП завжди стоїть пізніше від запису у ЗМІСТі.
    4. Шукаємо перший РОЗДІЛ після останнього ВСТУПу.
    5. Фолбек: якщо ВСТУП не знайдено, шукаємо РОЗДІЛ після великого
       розриву (>_MIN_GAP_AFTER_TOC рядків) — у ЗМІСТі розділи ідуть підряд,
       а реальний РОЗДІЛ 1 відірваний від ЗМІСТу великим блоком тексту.

    Рух НАЗАД принциповий: саме він відрізняє справжній заголовок від запису
    у ЗМІСТі. DOCX-гілка раніше мала власний прохід ВПЕРЕД і через це
    відкривала зону аналізу прямо на ЗМІСТі.
    """
    if search_end_idx is None:
        search_end_idx = len(texts) - 1

    # 2. Шукаємо ВИСНОВКИ рухаючись НАЗАД — обходимо ЗМІСТ.
    #    Бібліографія та додатки лише звужують межу: справжній кінець
    #    змістовної частини лежить вище за них.
    content_end_idx = search_end_idx
    for i in range(search_end_idx, -1, -1):
        line = texts[i]
        if _is_toc_entry(line):
            continue
        if _is_section_trigger(line, POST_CONTENT_HEADERS, exact=True):
            content_end_idx = max(i - 1, 0)
            continue
        if _is_section_trigger(line, CONTENT_END_HEADERS, exact=True):
            content_end_idx = max(i - 1, 0)
            break

    # 3. Шукаємо останній ВСТУП до content_end_idx
    last_vstup_idx = -1
    for i in range(content_end_idx):
        line = texts[i]
        if _is_toc_entry(line):
            continue
        if _is_section_trigger(line, ["ВСТУП"], exact=True):
            last_vstup_idx = i

    start_search_from = last_vstup_idx if last_vstup_idx != -1 else 0

    # 4. Шукаємо перший РОЗДІЛ після останнього ВСТУПу
    # (включно з content_end_idx — реальний заголовок РОЗДІЛУ може стояти
    # прямо перед ВИСНОВКАМИ, тобто саме на цьому індексі)
    content_start_idx = None
    for i in range(start_search_from, content_end_idx + 1):
        line = texts[i]
        if _is_toc_entry(line):
            continue
        if _is_section_trigger(line, CHAPTER_HEADERS, exact=False):
            content_start_idx = i
            break

    # 5. Фолбек: шукаємо РОЗДІЛ після великого розриву (ознака виходу зі ЗМІСТу)
    if content_start_idx is None:
        chap_indices = []
        for i in range(content_end_idx + 1):
            line = texts[i]
            if _is_toc_entry(line):
                continue
            if _is_section_trigger(line, CHAPTER_HEADERS, exact=False):
                chap_indices.append(i)

        if chap_indices:
            content_start_idx = chap_indices[0]
            for j in range(len(chap_indices) - 1):
                gap = chap_indices[j + 1] - chap_indices[j]
                if gap > _MIN_GAP_AFTER_TOC:
                    content_start_idx = chap_indices[j + 1]
                    break

    if content_start_idx is None:
        raise ContentBoundsNotFoundError(
            "Не вдалося знайти початок змістовних розділів (РОЗДІЛ 1 тощо)."
        )

    return content_start_idx, content_end_idx


def extract_content_bounds(
    lines: list[LineItem],
    biblio_start_page: int | None,
) -> tuple[int, int]:
    """
    Межі змістовних розділів для рядків з номерами сторінок (PDF).
    Верхня межа пошуку — перший рядок сторінки, на якій починається
    бібліографія.
    """
    search_end_idx = None
    if biblio_start_page is not None:
        for i, item in enumerate(lines):
            if item.get("page") == biblio_start_page:
                search_end_idx = i
                break

    return find_content_bounds_in_texts(
        [item["line"] for item in lines], search_end_idx
    )


# ---------------------------------------------------------------------------
# Підрахунок речень
# ---------------------------------------------------------------------------

_ABBR_RE = re.compile(
    r'\b(табл|таб|рис|див|стор|ст|с|п|ч|т|д|дод|вид|грн|млн|млрд|кг|км|га|ін|тис|проф|доц|акад)\.',
    re.IGNORECASE | re.UNICODE,
)

# Багатоскладові номери на кшталт "1.1." / "2.3.4." (нумерація таблиць,
# рисунків, розділів) — крапки всередині них не є кінцем речення.
# Часто йдуть одразу після скорочення "табл./рис." (напр. "табл. 1.1.").
_DECIMAL_NUM_RE = re.compile(r'\d+(?:\.\d+)+\.?')

# Lookbehind без \b — фіксована довжина, стабільний у всіх версіях Python
# Ігноруємо крапку після одиночної великої літери (ініціали: В., А.)
_SENTENCE_END = re.compile(
    r'(?<![А-ЯІЇЄҐA-Z]\.)(?<=[.!?])\s+(?=[А-ЯІЇЄҐA-Z])'
)


def _count_sentences(text: str) -> int:
    """Підрахунок кількості речень у тексті з ігноруванням скорочень."""
    cleaned = _ABBR_RE.sub(lambda m: m.group(0).replace(".", "\x00"), text)
    cleaned = _DECIMAL_NUM_RE.sub(lambda m: m.group(0).replace(".", "\x00"), cleaned)
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
    """
    Витягує абзаци з PDF у діапазоні сторінок [start_page, end_page].

    Нова логіка не покладається на page.get_text("blocks"), бо в складних PDF
    один логічний абзац часто розбивається на кілька дрібних фізичних блоків.
    Замість цього беремо page.get_text("dict"), збираємо текст построково і
    склеюємо сусідні рядки в абзац, поки між ними немає великого вертикального
    розриву.
    """
    import fitz

    LETTER_RE = re.compile(r'[А-Яа-яІіЇїЄєҐґA-Za-z]')
    LINE_GAP_FACTOR = 1.5  # розрив > 1.5 висоти рядка = новий абзац

    result = []
    idx = 0

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            if start_page and page_num < start_page:
                continue
            if end_page and page_num > end_page:
                break

            text_dict = page.get_text("dict")
            para_lines: list[str] = []
            prev_bottom = None
            prev_line_height = None

            def flush_para() -> None:
                nonlocal idx
                text = re.sub(r'\s+', ' ', " ".join(para_lines).strip())
                if len(text) >= MIN_BLOCK_CHARS and LETTER_RE.search(text):
                    result.append(ParagraphItem(
                        text=text,
                        page=page_num,
                        sentence_count=_count_sentences(text),
                        para_index=idx,
                    ))
                    idx += 1
                para_lines.clear()

            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue

                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    if not line_text:
                        continue

                    bbox = line.get("bbox", [0, 0, 0, 0])
                    top = bbox[1]
                    bottom = bbox[3]
                    line_height = bottom - top

                    if prev_bottom is not None and prev_line_height is not None:
                        gap = top - prev_bottom
                        if gap > prev_line_height * LINE_GAP_FACTOR:
                            flush_para()

                    para_lines.append(line_text)
                    prev_bottom = bottom
                    if line_height > 0:
                        prev_line_height = line_height

            flush_para()

    return result


def _extract_paragraphs_docx(file_bytes: bytes) -> list[ParagraphItem]:
    """
    Витягує абзаци з DOCX у межах змістовних розділів.

    Межі рахує те саме ядро, що й для PDF (find_content_bounds_in_texts),
    тільки над текстами параграфів. Власного проходу ВПЕРЕД більше немає:
    саме він раніше відкривав зону аналізу на записі ЗМІСТу «РОЗДІЛ 1 …\t12»
    і тягнув у підрахунок ЗМІСТ, ВСТУП та АНОТАЦІЮ.

    Кидає ContentBoundsNotFoundError, якщо змістовних розділів не видно.
    """
    import io
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paras = list(doc.paragraphs)
    texts = [p.text.strip() for p in paras]

    start_idx, end_idx = find_content_bounds_in_texts(texts)

    result: list[ParagraphItem] = []
    last_heading: str | None = None
    idx = 0

    for i, para in enumerate(paras):
        style_name = para.style.name if para.style else ""
        is_heading = "heading" in style_name.lower()
        text = texts[i]

        # Заголовки відстежуємо і до початку зони — щоб context_heading
        # правильно вказував на розділ, у якому лежить перший абзац.
        if is_heading and text:
            last_heading = text

        if i < start_idx or i > end_idx:
            continue

        if is_heading:
            continue
        if len(text) < MIN_BLOCK_CHARS:
            continue
        if not re.search(r'[А-Яа-яІіЇїЄєҐґA-Za-z]', text):
            continue
        if _TOC_LEADER_RE.search(text):
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
    content_start_page: int | None = None,
    content_end_page: int | None = None,
) -> list[ParagraphItem]:
    """Єдина точка входу для PDF і DOCX."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_paragraphs_pdf(file_bytes, content_start_page, content_end_page)
    elif lower.endswith(".docx"):
        return _extract_paragraphs_docx(file_bytes)
    return []


# ---------------------------------------------------------------------------
# Перевірка наявності посилань
# ---------------------------------------------------------------------------

def paragraph_has_citation(text: str) -> bool:
    """
    True якщо в абзаці є хоча б одне посилання на джерело.

    Недостатньо просто збігу дужки: «a[0]» і «[2020]» дужку дають, а джерела —
    ні. Тому вміст дужки має розгорнутися хоча б в один валідний номер.
    """
    return any(expand_bracket(m.group(1)) for m in BRACKET_RE.finditer(text))


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
    lines: list[LineItem],
    biblio_start_page: int | None,
) -> ParagraphGapResult:
    is_docx = filename.lower().endswith(".docx")

    if is_docx:
        # DOCX рахує свої межі сам, по параграфах документа. Викликати тут
        # extract_content_bounds не можна: його результат DOCX-гілці не
        # потрібен, зате він уміє кинути ContentBoundsNotFoundError і
        # обірвати аналіз, який чудово пройшов би без нього.
        paragraphs = extract_paragraphs(file_bytes, filename)
    else:
        content_start_idx, content_end_idx = extract_content_bounds(
            lines, biblio_start_page
        )
        paragraphs = extract_paragraphs(
            file_bytes,
            filename,
            lines[content_start_idx].get("page"),
            lines[content_end_idx].get("page"),
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
