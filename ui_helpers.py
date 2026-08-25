"""
ui_helpers.py
Чиста логіка шару інтерфейсу, винесена з app.py.

app.py виконує код Streamlit на рівні модуля, тому імпортувати його в тестах
неможливо. Усе, що можна перевірити без запущеного Streamlit, живе тут.
"""

from __future__ import annotations

from typing import Any, Iterable, MutableMapping
import hashlib

from parser.types import LineItem


def is_compare_mode(query_params: MutableMapping[str, Any]) -> bool:
    """Повертає True лише для окремого режиму порівняння двох робіт."""
    value = query_params.get("mode")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value == "compare"


# ---------------------------------------------------------------------------
# Ключі стану, прив'язані до конкретного завантаженого файлу
# ---------------------------------------------------------------------------
# Streamlit зберігає session_state між перезапусками скрипта, зокрема й після
# завантаження ІНШОГО файлу. Ці ключі тримають результати аналізу попереднього
# документа й мають скидатися при зміні файлу — інакше користувач побачить
# (і завантажить) результати від файлу A під іменем файлу B.
FILE_SCOPED_KEYS: tuple[str, ...] = (
    "highlighted_pdf",
    "empty_pages",
    "tracked_pages_count",
    "para_gap_result",
)

_FILE_KEY = "current_file_key"

PAIR_SCOPED_KEYS: tuple[str, ...] = (
    "compare_result",
    "compare_visible_limit",
    "compare_type_filter",
    "compare_sort",
    "compare_show_normative",
)
_PAIR_KEY = "current_compare_pair_key"


def make_file_key(name: str, size: int, file_id: str | None = None) -> str:
    """
    Стабільний ідентифікатор завантаженого файлу.

    file_id від Streamlit унікальний для кожного завантаження; якщо його немає
    (інша версія бібліотеки) — відкочуємося на пару «ім'я + розмір».
    """
    return file_id or f"{name}:{size}"


def reset_file_scoped_state(state: MutableMapping[str, Any], file_key: str) -> bool:
    """
    Скидає результати попереднього файлу, якщо завантажено інший.

    Повертає True, якщо скидання відбулося.
    """
    if state.get(_FILE_KEY) == file_key:
        return False
    state[_FILE_KEY] = file_key
    for key in FILE_SCOPED_KEYS:
        state.pop(key, None)
    return True


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_pair_key(checked: bytes, source: bytes) -> str:
    """Ролі входять до ключа, тому A/B та B/A — різні порівняння."""
    return f"checked:{file_sha256(checked)}|source:{file_sha256(source)}"


def reset_pair_scoped_state(state: MutableMapping[str, Any], pair_key: str) -> bool:
    if state.get(_PAIR_KEY) == pair_key:
        return False
    state[_PAIR_KEY] = pair_key
    for key in PAIR_SCOPED_KEYS:
        state.pop(key, None)
    return True


# ---------------------------------------------------------------------------
# Форматування
# ---------------------------------------------------------------------------

def format_number_ranges(pages: Iterable) -> str:
    """
    Стискає список чисел у рядок діапазонів: [1,2,3,7,9,10,11] → "1–3, 7, 9–11".
    Вхід може бути невідсортованим; порожній вхід дає порожній рядок.
    """
    nums = sorted(int(p) for p in pages)
    if not nums:
        return ""

    ranges: list[str] = []
    start = end = nums[0]
    for p in nums[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}–{end}" if end > start else str(start))
            start = end = p
    ranges.append(f"{start}–{end}" if end > start else str(start))
    return ", ".join(ranges)


def lines_to_tuple(lines: list[LineItem]) -> tuple[tuple[str, int | None], ...]:
    """
    Перетворює list[LineItem] у hashable-tuple для передачі в @st.cache_data:
    dict не хешується, tuple — хешується.
    """
    return tuple((item["line"], item.get("page")) for item in lines)


def tuple_to_lines(pairs: tuple[tuple[str, int | None], ...]) -> list[LineItem]:
    """Зворотне перетворення для tuple → list[LineItem]."""
    return [{"line": line, "page": page} for line, page in pairs]
