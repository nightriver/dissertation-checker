"""
citations.py — Citation parser with full format support.

Supported formats:
  [1]                     → {1}
  [1, 15]                 → {1, 15}   no ';' → comma = source separator
  [1; 3; 7]               → {1, 3, 7}
  [1, 15; 3; 5, 20-25]    → {1, 3, 5} has ';' → comma = page separator
  [89, с. 11]             → {89}       Cyrillic page marker
  [31, с. 70; 55; 59, с. 26] → {31, 55, 59}
  [250, с. 11-19]         → {250}
  [15-18] / [15–18] / [15−18] → {15..18}  all dash variants
  \uF05B94; 108\uF05D     → {94, 108}  Wingdings/Symbol PUA brackets

Comma semantics (depends on presence of ';'):
  WITH    ';': comma = page separator → take only first token of each ;-group
  WITHOUT ';': comma = source separator → all tokens are sources

Fixed bugs:
  #1 — Multiline brackets (PyMuPDF splits lines): full-text join
  #2 — U+2212 MINUS SIGN as range dash
  #3 — U+F05B/F05D Wingdings brackets
  #4 — Comma-only source lists [3, 7, 11]
  #5 — Cyrillic 'с.' page marker breaking bracket match
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# Регулярні вирази
# ---------------------------------------------------------------------------

# Всі варіанти тире/дефісу у діапазонах:
#   U+002D  -  дефіс
#   U+2013  –  en-dash
#   U+2212  −  математичний мінус (частий у Word→PDF)
_DASHES = "[\u002d\u2013\u2212]"

# Допустимі символи всередині цитатних дужок:
#   цифри, пробіли, ; , . тире-варіанти
#   U+0441 с  / U+0421 С  — кириллична буква «с.» при вказівці сторінки
#   (НЕ додаємо латинські c/C — уникаємо ризику хибних спрацювань)
_INNER = r"[\d\s;,\u002d\u2013\u2212\.\u0441\u0421]"

# Знаходить цитатні конструкції з будь-яким типом дужок:
#   стандартні:    [ ... ]
#   Wingdings PUA: \uF05B ... \uF05D  (Symbol/Wingdings у MS Word → PDF)
_BRACKET_RE = re.compile(
    r"(?:\[|\uF05B)(" + _INNER + r"+)(?:\]|\uF05D)"
)

# Діапазон: два числа через будь-який тире-символ
_RANGE_RE = re.compile(r"^(\d+)\s*" + _DASHES + r"\s*(\d+)$")

# ---------------------------------------------------------------------------
# Ядро розбору
# ---------------------------------------------------------------------------

def _parse_token(token: str) -> set[int]:
    """Розбирає один токен: число або діапазон → множина джерел."""
    token = token.strip()
    if not token:
        return set()
    m = _RANGE_RE.match(token)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi and (hi - lo) <= 200:
            return set(range(lo, hi + 1))
        return set()
    if token.isdigit():
        return {int(token)}
    return set()


def _expand_bracket(content: str) -> set[int]:
    """
    Розбирає вміст між дужками → множина номерів джерел.

    Правило коми:
      • Є ';'  → кома = роздільник «джерело / сторінка»
                 [89, с. 11; 98] → беремо перший токен кожної ;-групи → {89, 98}
      • Немає ';' → кома = роздільник джерел
                 [3, 7, 11] → {3, 7, 11}
    """
    result: set[int] = set()
    if ";" in content:
        for group in content.split(";"):
            group = group.strip()
            if group:
                # Беремо тільки перший токен до першої коми (після коми — сторінка)
                result |= _parse_token(group.split(",")[0])
    else:
        for token in content.split(","):
            result |= _parse_token(token)
    return result


# ---------------------------------------------------------------------------
# Публічний API
# ---------------------------------------------------------------------------

def find_citations(body_lines: list[dict]) -> set[int]:
    """
    Знаходить усі номери використаних джерел у зоні body.

    Підхід: зливаємо весь body в один рядок через пробіл.
    Це на 100% вирішує проблему дужок розірваних на будь-яку кількість рядків
    (PyMuPDF може розірвати довгий список на 3-4 рядки).

    Ризик хибних збігів мінімальний: _INNER містить лише цифри, пробіли
    та розділові знаки — суцільний текст з літерами не матчиться.
    """
    full_text = " ".join(item["line"] for item in body_lines)
    used: set[int] = set()
    for match in _BRACKET_RE.finditer(full_text):
        used.update(_expand_bracket(match.group(1)))
    return used


def compare(bibliography: dict[int, str], citations: set[int]) -> dict:
    """
    Порівнює словник джерел з множиною використаних посилань.

    Повертає dict:
        all_sources : set[int]
        used        : set[int]
        orphans     : set[int]  — є в списку, але не цитуються
        phantom     : set[int]  — цитуються, але нема в списку
    """
    all_sources = set(bibliography.keys())
    return {
        "all_sources": all_sources,
        "used": citations & all_sources,
        "orphans": all_sources - citations,
        "phantom": citations - all_sources,
    }
