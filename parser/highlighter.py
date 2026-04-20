"""
highlighter.py
Режим "Асистент антиплагіату":
створює копію PDF, де всі посилання виду [81, с. 162] підсвічені червоним.
Підтримує цитати, розірвані на два рядки / два блоки.

highlight_citations_pdf() повертає tuple:
    [0] bytes       — модифікований PDF
    [1] list[int]   — сторінки (1-індексовані) без жодного посилання
                      (перші skip_first сторінок та бібліографія виключені)
    [2] int         — загальна кількість відстежуваних сторінок (знаменник %)
"""

from __future__ import annotations
import re
import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Регулярний вираз для пошуку посилань
# ---------------------------------------------------------------------------

CITATION_PATTERN = re.compile(
    r"(?:\[|\uF05B)"          # відкриваюча дужка (звичайна або Wingdings)
    r"\s*"                    # можливий пробіл після дужки
    r"\d"                     # перший символ — обов'язково цифра
    r"[^\]\uF05D]{0,250}"     # вміст, максимум 250 символів
    r"(?:\]|\uF05D)",         # закриваюча дужка
    re.UNICODE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Внутрішні функції
# ---------------------------------------------------------------------------

def _build_page_spans(words: list) -> tuple[str, list[dict]]:
    """
    Склеює ВСІ слова сторінки в суцільний рядок, зберігаючи
    маппінг символьних індексів → координати (quad) кожного слова.
    """
    full_text = ""
    word_spans = []
    current_idx = 0
    prev_block = None

    for w in words:
        x0, y0, x1, y1, text, block_no = w[0], w[1], w[2], w[3], w[4], w[5]
        rect = fitz.Rect(x0, y0, x1, y1)
        quad = rect.quad

        if prev_block is not None and block_no != prev_block:
            full_text += "\n"
            current_idx += 1

        start_idx = current_idx
        end_idx = start_idx + len(text)

        word_spans.append({
            "start": start_idx,
            "end":   end_idx,
            "quad":  quad,
        })

        full_text += text + " "
        current_idx = end_idx + 1

        prev_block = block_no

    return full_text, word_spans


def _highlight_page(page: fitz.Page) -> bool:
    """
    Знаходить усі посилання на сторінці, додає червону highlight-анотацію
    і повертає True якщо хоча б одне посилання знайдено, False — якщо нічого.
    """
    words = page.get_text("words")
    if not words:
        return False

    full_text, word_spans = _build_page_spans(words)

    found = False
    for match in CITATION_PATTERN.finditer(full_text):
        found = True
        m_start, m_end = match.span()

        quads = [
            ws["quad"]
            for ws in word_spans
            if ws["start"] < m_end and ws["end"] > m_start
        ]

        if quads:
            annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=(1, 0.2, 0.2))  # червоний
            annot.update()

    return found


# ---------------------------------------------------------------------------
# Публічний API
# ---------------------------------------------------------------------------

def highlight_citations_pdf(
    pdf_bytes: bytes,
    biblio_start_page: int | None,
    skip_first: int = 2,
) -> tuple[bytes, list[int], int]:
    """
    Повертає копію PDF з підсвіченими посиланнями + статистику порожніх сторінок.

    Args:
        pdf_bytes:         вміст оригінального PDF файлу
        biblio_start_page: номер першої сторінки бібліографії (1-індексований),
                           або None якщо не визначено
        skip_first:        кількість перших сторінок, які НЕ включаються до
                           відстеження (титул, зміст тощо). За замовчуванням 2.
                           Підсвітка на цих сторінках все одно виконується.

    Returns:
        tuple:
            [0] bytes      — модифікований PDF
            [1] list[int]  — відсортований список 1-індексованих номерів сторінок
                             без жодного посилання (перші skip_first і бібліографія
                             виключені)
            [2] int        — кількість відстежуваних сторінок (знаменник для %)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Остання сторінка тіла (0-індекс PyMuPDF)
    last_body_idx = len(doc) - 1
    if biblio_start_page and biblio_start_page > 1:
        last_body_idx = biblio_start_page - 2   # biblio_start_page - 1 → 0-idx, мінус 1

    pages_without: list[int] = []
    tracked_count = 0

    for page_idx in range(last_body_idx + 1):
        page_num = page_idx + 1   # 1-індексований
        has_citations = _highlight_page(doc[page_idx])

        # Перші skip_first сторінок — підсвічуємо, але не відстежуємо
        if page_num <= skip_first:
            continue

        tracked_count += 1
        if not has_citations:
            pages_without.append(page_num)

    return doc.tobytes(deflate=True), pages_without, tracked_count
