"""
highlighter.py
Режим "Асистент антиплагіату":
створює копію PDF, де всі посилання виду [81, с. 162] підсвічені червоним.
Підтримує цитати, розірвані на два рядки / два блоки.
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
    r"[^\]\uF05D]{0,250}"     # вміст, максимум 250 символів (більше — для довгих розірваних)
    r"(?:\]|\uF05D)",         # закриваюча дужка
    re.UNICODE | re.DOTALL,   # DOTALL дозволяє . збігатися з \n
)


# ---------------------------------------------------------------------------
# Внутрішні функції
# ---------------------------------------------------------------------------

def _build_page_spans(words: list) -> tuple[str, list[dict]]:
    """
    Склеює ВСІ слова сторінки в суцільний рядок, зберігаючи
    маппінг символьних індексів → координати (quad) кожного слова.

    На відміну від попередньої версії, не групує по block_no —
    це дозволяє ловити цитати, розірвані між блоками/рядками.

    Між словами вставляємо пробіл; між різними block_no — '\n',
    щоб регулярка не «зшивала» непов'язані фрагменти тексту,
    але все одно могла перетинати межу рядка всередині однієї цитати.
    """
    full_text = ""
    word_spans = []
    current_idx = 0
    prev_block = None

    for w in words:
        x0, y0, x1, y1, text, block_no = w[0], w[1], w[2], w[3], w[4], w[5]
        rect = fitz.Rect(x0, y0, x1, y1)
        quad = rect.quad

        # Між різними блоками ставимо '\n' — регулярка з re.DOTALL його проковтне,
        # але це не дасть з'єднати "кінець абзацу[" з "початок наступного]"
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
        current_idx = end_idx + 1  # +1 враховує пробіл

        prev_block = block_no

    return full_text, word_spans


def _highlight_page(page: fitz.Page) -> None:
    """
    Знаходить усі посилання на сторінці і додає червону highlight-анотацію.
    Один виклик add_highlight_annot з кількома quads коректно охоплює
    цитату, розірвану на два рядки.
    """
    words = page.get_text("words")  # (x0,y0,x1,y1, text, block_no, line_no, word_no)
    if not words:
        return

    full_text, word_spans = _build_page_spans(words)

    for match in CITATION_PATTERN.finditer(full_text):
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
