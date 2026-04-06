"""
citations.py
Пошук усіх посилань у зоні body та розбір їхнього вмісту.

Формат посилань у корпусі (українські юридичні дисертації):
  [1]          → {1}
  [1, 15]      → {1}          (15 — номер сторінки, ігнорується)
  [1; 3; 7]    → {1, 3, 7}
  [15-18]      → {15,16,17,18}  (розгортання діапазону)
  [15–18]      → те саме (em-dash / en-dash)
  [1, 15; 3; 5, 20-25; 40, 20-25, 100; 60, 145, 150]  → {1,3,5,40,60}

Кома завжди відокремлює номер джерела від номера сторінки,
а НЕ два різних джерела (підтверджено замовником).
"""

from __future__ import annotations
import re


# Знаходить усі конструкції виду  [...]  де всередині є цифри
_BRACKET_RE = re.compile(r"\[[\d\s;,–\-\.]+\]")

# Визначає діапазон: два числа через дефіс або тире
_RANGE_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)$")


def _parse_group(group: str) -> int | None:
    """
    Розбирає одну «групу» між крапками з комою.
    Повертає номер джерела або None, якщо група не розпізнана.

    group = "1"          → 1
    group = "1, 15"      → 1     (15 — сторінка, ігнорується)
    group = "15-18"      → ???   → None (діапазон без коми: перший токен — ціла конструкція)
    group = "5, 20-25"   → 5
    """
    # Беремо перший токен до першої коми
    first_token = group.split(",")[0].strip()

    # Перевіряємо: чи це діапазон  15-18 / 15–18
    # (трапляється коли весь вміст дужок — один діапазон без коми)
    range_m = _RANGE_RE.match(first_token)
    if range_m:
        # Діапазон як перший токен — це діапазон джерел.
        # Повертаємо sentinel None; виклик expand_range обробить його окремо.
        return None

    # Звичайне число
    if first_token.isdigit():
        return int(first_token)

    return None


def _expand_bracket(content: str) -> set[int]:
    """
    content — рядок між [ та ], наприклад:
        "1, 15; 3; 5, 20-25; 40, 20-25, 100; 60, 145, 150"
    Повертає множину номерів джерел.
    """
    result: set[int] = set()
    groups = content.split(";")

    for raw_group in groups:
        group = raw_group.strip()
        if not group:
            continue

        first_token = group.split(",")[0].strip()
        range_m = _RANGE_RE.match(first_token)

        if range_m:
            # Весь перший токен — це діапазон (без коми перед числом сторінки)
            lo, hi = int(range_m.group(1)), int(range_m.group(2))
            if lo <= hi and (hi - lo) <= 200:  # захист від сміттєвих діапазонів
                result.update(range(lo, hi + 1))
        else:
            num = _parse_group(group)
            if num is not None:
                result.add(num)

    return result


def find_citations(body_lines: list[dict]) -> set[int]:
    """
    Шукає всі посилання у зоні body та повертає множину
    унікальних номерів використаних джерел.

    body_lines: список {"line": str, "page": int | None}
    """
    used: set[int] = set()

    for item in body_lines:
        for match in _BRACKET_RE.finditer(item["line"]):
            # Зрізаємо [ та ]
            inner = match.group()[1:-1]
            used.update(_expand_bracket(inner))

    return used


# ---------------------------------------------------------------------------
# Зручна функція — кінцевий аналіз
# ---------------------------------------------------------------------------

def compare(
    bibliography: dict[int, str],
    citations: set[int],
) -> dict:
    """
    Порівнює словник джерел з множиною використаних посилань.

    Повертає dict:
        all_sources   : set[int]
        used          : set[int]
        orphans       : set[int]   — є в списку, але не цитуються
        phantom       : set[int]   — цитуються, але нема в списку (ігноруються в UI)
    """
    all_sources = set(bibliography.keys())
    used = citations & all_sources
    orphans = all_sources - citations
    phantom = citations - all_sources

    return {
        "all_sources": all_sources,
        "used": used,
        "orphans": orphans,
        "phantom": phantom,
    }
