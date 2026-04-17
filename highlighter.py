"""
highlighter.py
Режим "Асистент антиплагіату":
створює копію PDF, де всі посилання виду [81, с. 162] підсвічені червоним.
"""

from __future__ import annotations
import re
import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Регулярний вираз для пошуку посилань
# ---------------------------------------------------------------------------

CITATION_PATTERN = re.compile(
    r"(?:\[|\uF05B)"          # відкриваюча дужка (звичайна або Wingdings)
    r"\s*"                    # можливий пробіл після дужки — для [ 8 2 , с. 75]
    r"\d"                     # перший символ — обов'язково цифра (не захопить [див. рис. 1])
    r"[^\]\uF05D]{0,150}"     # вміст, максимум 150 символів
    r"(?:\]|\uF05D)",         # закриваюча дужка
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Внутрішні функції
# ---------------------------------------------------------------------------

def _build_word_spans(block_words: list) -> tuple[str, list[dict]]:
    """
    Склеює слова блоку в суцільний рядок і будує маппінг
    символьних індексів до координат (rect, quad) кожного слова.

    Повертає:
        full_text  — суцільний рядок з пробілами між словами
        word_spans — список dict з ключами start, end, rect, quad
    """
    full_text = ""
    word_spans = []
    current_idx = 0

    for w in block_words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        rect = fitz.Rect(x0, y0, x1, y1)
        quad = rect.quad  # fitz.Quad — потрібен для add_highlight_annot

        start_idx = current_idx
        end_idx = start_idx + len(text)

        word_spans.append({
            "start": start_idx,
            "end": end_idx,
            "rect": rect,
            "quad": quad,
        })

        full_text += text + " "
        current_idx = end_idx + 1  # +1 враховує пробіл між словами

    return full_text, word_spans


def _highlight_page(page: fitz.Page) -> None:
    """
    Знаходить усі посилання на сторінці і додає червону highlight-анотацію.
    Групування по block_no ізолює колонтитули та підписи від основного тексту.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, text, block_no, line_no, word_no)
    if not words:
        return

    # Групуємо по block_no (w[5])
    blocks: dict[int, list] = {}
    for w in words:
        blocks.setdefault(w[5], []).append(w)

    for block_words in blocks.values():
        full_text, word_spans = _build_word_spans(block_words)

        for match in CITATION_PATTERN.finditer(full_text):
            m_start, m_end = match.span()

            # Збираємо quads усіх слів, що перетинаються з діапазоном збігу
            quads = [
                ws["quad"]
                for ws in word_spans
                if ws["start"] < m_end and ws["end"] > m_start
            ]

            if quads:
                # add_highlight_annot приймає список quads — коректно обробляє
                # посилання, розірвані на два рядки (один annot, кілька quads)
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=(1, 0.2, 0.2))  # червоний, текст читається
                annot.update()


# ---------------------------------------------------------------------------
# Публічний API
# ---------------------------------------------------------------------------

def highlight_citations_pdf(
    pdf_bytes: bytes,
    biblio_start_page: int | None,
) -> bytes:
    """
    Повертає копію PDF з підсвіченими посиланнями в тексті.
    Сторінки бібліографії не обробляються.

    Args:
        pdf_bytes:         вміст оригінального PDF файлу
        biblio_start_page: номер першої сторінки бібліографії (1-індексований),
                           або None якщо не визначено

    Returns:
        bytes: вміст модифікованого PDF

    Математика сторінок:
        biblio_start_page = 150 (1-індекс) → індекс PyMuPDF = 149
        Обробляємо до індексу 148 включно → last_page = 150 - 2 = 148
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    last_page = len(doc) - 1
    if biblio_start_page and biblio_start_page > 1:
        last_page = biblio_start_page - 2

    for page_num in range(last_page + 1):
        _highlight_page(doc[page_num])

    return doc.tobytes(deflate=True)
