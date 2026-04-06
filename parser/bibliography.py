"""
bibliography.py
Розбивка тексту на три зони (body / bibliography / after)
та парсинг багаторядкових бібліографічних записів.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Константи — заголовки й стоп-слова
# ---------------------------------------------------------------------------

BIBLIO_HEADERS: list[str] = [
    "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
    "СПИСОК ЛІТЕРАТУРИ",
    "СПИСОК ВИКОРИСТАНОЇ ЛІТЕРАТУРИ",
    "ВИКОРИСТАНІ ДЖЕРЕЛА",
    "БІБЛІОГРАФІЯ",
    "БІБЛІОГРАФІЧНИЙ СПИСОК",
    "REFERENCES",
    "LITERATURE",
]

STOP_WORDS: list[str] = [
    "СПИСОК ПУБЛІКАЦІЙ ЗДОБУВАЧА",
    "ДОДАТКИ",
    "ДОДАТОК",
    "АНОТАЦІЯ",
    "ABSTRACT",
]

# Патерн початку нового бібліографічного запису:
# підтримує  "1. Текст..."  і  "[1] Текст..."
# Bug 3 fix: обмежуємо номер до MAX_SOURCE_NUM, щоб рядки продовження
# на зразок "2001. – № 29. – С. 2." (рік видання) не трактувались як
# новий запис #2001.
MAX_SOURCE_NUM = 999
# Text after number is optional: PyMuPDF sometimes puts number on its own line.
# "11.\n\nБайкулатова..." → "11." matches, empty group(2), next lines become content.
_ENTRY_START = re.compile(r"^\s*(?:\[)?(\d+)(?:\.|\])\s*(.*)")

# Максимально допустимий номер джерела.
# Числа понад 999 — це роки в тексті записів (напр. "2005. URL: ...")
# а не порядкові номери. Реальна дисертація має щонайбільше ~500 джерел.
_MAX_SOURCE_NUM = 999


def _is_valid_entry_num(n: int) -> bool:
    """True якщо n може бути порядковим номером джерела (не рік, не сміття)."""
    return 1 <= n <= _MAX_SOURCE_NUM


# ---------------------------------------------------------------------------
# Допоміжні функції — пошук меж зон
# ---------------------------------------------------------------------------

def _normalize(line: str) -> str:
    """Верхній регістр, стиснені пробіли — для порівняння."""
    return re.sub(r"\s+", " ", line.strip().upper())


def _is_biblio_header(line: str) -> bool:
    n = _normalize(line)
    return any(n == h or n.startswith(h) for h in BIBLIO_HEADERS)


def _is_stop_word(line: str) -> bool:
    n = _normalize(line)
    return any(n == s or n.startswith(s) for s in STOP_WORDS)


# ---------------------------------------------------------------------------
# Публічний API
# ---------------------------------------------------------------------------

@dataclass
class ZoneSplitResult:
    body: list[dict]           # зона 1 — основний текст
    bibliography: list[dict]   # зона 2 — список літератури
    after: list[dict]          # зона 3 — ігнорується
    biblio_header_line: str | None = None   # знайдений заголовок
    biblio_start_page: int | None = None    # сторінка початку (PDF)
    found_automatically: bool = True


class BibliographyNotFoundError(Exception):
    pass


def split_zones(lines: list[dict]) -> ZoneSplitResult:
    """
    Ділить список рядків на три зони.
    Шукає заголовок бібліографії з КІНЦЯ (береться останній збіг),
    потім шукає вперед перше стоп-слово.

    lines: список {"line": str, "page": int | None}
    """
    biblio_start: int | None = None
    biblio_header_text: str | None = None
    biblio_start_page: int | None = None

    # Сканування з кінця → знаходимо останній заголовок бібліографії
    for i in range(len(lines) - 1, -1, -1):
        if _is_biblio_header(lines[i]["line"]):
            biblio_start = i
            biblio_header_text = lines[i]["line"].strip()
            biblio_start_page = lines[i].get("page")
            break

    if biblio_start is None:
        raise BibliographyNotFoundError(
            "Список літератури не знайдено автоматично. "
            "Вкажіть розташування вручну."
        )

    # Пошук стоп-слова після заголовка → кінець бібліографії
    biblio_end: int = len(lines)
    for i in range(biblio_start + 1, len(lines)):
        if _is_stop_word(lines[i]["line"]):
            biblio_end = i
            break

    return ZoneSplitResult(
        body=lines[:biblio_start],
        bibliography=lines[biblio_start:biblio_end],
        after=lines[biblio_end:],
        biblio_header_line=biblio_header_text,
        biblio_start_page=biblio_start_page,
        found_automatically=True,
    )


def split_zones_manual(
    lines: list[dict],
    header_text: str,
    start_page: int | None = None,
) -> ZoneSplitResult:
    """
    Ручний режим: шукаємо перший рядок, що містить header_text (без урахування
    регістру). Якщо задано start_page — шукаємо тільки на цій сторінці й далі.
    """
    header_norm = header_text.strip().upper()

    biblio_start: int | None = None
    for i, item in enumerate(lines):
        if start_page is not None and (item.get("page") or 0) < start_page:
            continue
        if header_norm in _normalize(item["line"]):
            biblio_start = i
            break

    if biblio_start is None:
        raise BibliographyNotFoundError(
            f"Рядок «{header_text}» не знайдено в документі."
        )

    biblio_end = len(lines)
    for i in range(biblio_start + 1, len(lines)):
        if _is_stop_word(lines[i]["line"]):
            biblio_end = i
            break

    return ZoneSplitResult(
        body=lines[:biblio_start],
        bibliography=lines[biblio_start:biblio_end],
        after=lines[biblio_end:],
        biblio_header_line=lines[biblio_start]["line"].strip(),
        biblio_start_page=lines[biblio_start].get("page"),
        found_automatically=False,
    )


def parse_bibliography(bibliography_lines: list[dict]) -> dict[int, str]:
    """
    Парсить зону bibliography → словник {номер: повний_текст}.
    Підтримує багаторядкові записи: рядки без патерну початку
    приєднуються до попереднього запису.

    Повертає порожній словник, якщо жодного запису не знайдено.
    """
    entries: dict[int, str] = {}
    current_num: int | None = None
    current_parts: list[str] = []

    def _flush():
        if current_num is not None and current_parts:
            entries[current_num] = " ".join(current_parts)

    for item in bibliography_lines:
        line = item["line"]
        m = _ENTRY_START.match(line)
        if m and _is_valid_entry_num(int(m.group(1))):
            _flush()
            current_num = int(m.group(1))
            text_part = m.group(2).strip()
            # Number may be alone on line; text starts on next line
            current_parts = [text_part] if text_part else []
        else:
            # Продовження попереднього запису (або заголовок зони — ігноруємо)
            if current_num is not None:
                stripped = line.strip()
                if stripped:
                    current_parts.append(stripped)

    _flush()
    return entries
