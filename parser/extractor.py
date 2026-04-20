"""
extractor.py
Витяг тексту з PDF і DOCX у вигляді списку рядків.
Кожен елемент: {"line": str, "page": int | None}
"""

from __future__ import annotations
import re
from typing import BinaryIO


MAX_FILE_SIZE = 30 * 1024 * 1024  # 30 МБ


class FileTooLargeError(Exception):
    pass


class ScannedPDFError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass


def _check_size(data: bytes) -> None:
    if len(data) > MAX_FILE_SIZE:
        raise FileTooLargeError("Файл завеликий. Максимальний розмір — 30 МБ.")


def extract_lines_from_pdf(data: bytes) -> list[dict]:
    """
    Повертає список {"line": str, "page": int} для кожного непорожнього
    візуального рядка документа. Сторінки нумеруються з 1.
    Якщо весь документ не містить тексту — кидає ScannedPDFError.
    """
    _check_size(data)

    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ImportError("Бібліотека PyMuPDF не встановлена.") from e

    result: list[dict] = []
    total_chars = 0

    with fitz.open(stream=data, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")
            total_chars += len(text.strip())
            for raw_line in text.splitlines():
                stripped = raw_line.rstrip()
                if stripped:
                    result.append({"line": stripped, "page": page_num})

    if total_chars == 0:
        raise ScannedPDFError(
            "Файл є скан-копією або захищеним PDF — текст недоступний."
        )

    return result


def extract_lines_from_docx(data: bytes) -> list[dict]:
    """
    Повертає список {"line": str, "page": None} для кожного непорожнього
    параграфа DOCX. Номер сторінки недоступний без рендерингу.
    """
    _check_size(data)

    import io

    try:
        from docx import Document
    except ImportError as e:
        raise ImportError("Бібліотека python-docx не встановлена.") from e

    doc = Document(io.BytesIO(data))
    result: list[dict] = []

    for para in doc.paragraphs:
        stripped = para.text.rstrip()
        if stripped:
            result.append({"line": stripped, "page": None})

    return result


def extract_lines(data: bytes, filename: str) -> list[dict]:
    """
    Диспетчер: визначає формат за розширенням та викликає відповідний екстрактор.
    filename використовується тільки для визначення розширення.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_lines_from_pdf(data)
    elif lower.endswith(".docx"):
        return extract_lines_from_docx(data)
    else:
        raise UnsupportedFormatError(
            f"Непідтримуваний формат файлу: «{filename}». "
            "Дозволено лише .pdf та .docx."
        )


def extract_dissertation_year(lines: list[dict], max_lines: int = 60) -> int | None:
    """
    Шукає рік написання дисертації в перших max_lines рядках.
    lines: list[dict] з ключами "line" та "page" — стандартна структура проєкту.
    Повертає int або None.
    """
    candidate_pattern = re.compile(r'\b(20[0-2]\d|19[89]\d)\b')
    candidates: list[int] = []

    for item in lines[:max_lines]:
        text_line = item["line"]
        for match in candidate_pattern.finditer(text_line):
            candidates.append(int(match.group(1)))

    return max(candidates) if candidates else None
