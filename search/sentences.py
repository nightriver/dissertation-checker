"""
search/sentences.py
Спільний поділ нормалізованого тексту на речення та вікна для каналів
відбору кандидатів. Специфікація — PLAN_SEARCH.md, §7 та §10.

Крок 3 (§22) реалізує лише межу речення за розділовими знаками
`. ! ? …`: далі йде пробіл/кінець тексту і наступний непорожній символ —
велика літера, цифра, відкривна лапка або тире (§10.1). Захист скорочень
(`_ABBR_RE`/`_DECIMAL_NUM_RE`), десяткових чисел, ініціалів та обробка
залишку речення на межі фізичної сторінки (`page_boundary_fragment`) —
крок 4/9 (§22, рядки 4 та 9): рядок 3 явно виключає завершеність меж
речень, рядок 4 явно обіцяє скорочення. Тому в цьому кроці незавершений
хвіст без термінальної пунктуації відкидається цілком (не повертається як
речення), а не склеюється й не позначається окремою причиною.
"""

from __future__ import annotations

_TERMINATORS = ".!?…"
_OPEN_QUOTE_CHARS = "«\"„“'"
_DASH_CHARS = "-–—"


def split_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """
    Повертає впорядкований кортеж піввідкритих символьних меж `(start, end)`
    завершених речень у `text`. Незавершений хвіст без термінальної
    пунктуації відкидається (див. docstring модуля).
    """
    if not text:
        return ()

    bounds: list[tuple[int, int]] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _TERMINATORS:
            j = i + 1
            while j < n and text[j] in _TERMINATORS:
                j += 1
            if j < n and text[j].isspace():
                k = j
                while k < n and text[k].isspace():
                    k += 1
                if k < n and (
                    text[k].isupper() or text[k].isdigit()
                    or text[k] in _OPEN_QUOTE_CHARS or text[k] in _DASH_CHARS
                ):
                    bounds.append(_trim(text, start, j))
                    start = k
                    i = k
                    continue
            elif j >= n:
                bounds.append(_trim(text, start, j))
                start = j
                i = j
                continue
        i += 1
    return tuple(b for b in bounds if b[0] < b[1])


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
