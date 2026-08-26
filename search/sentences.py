"""
search/sentences.py
Спільний поділ нормалізованого тексту на речення та вікна для каналів
відбору кандидатів. Специфікація — PLAN_SEARCH.md, §7 та §10.1, §13.

Межа речення — `.`, `!`, `?` або `…`, після якої є пробіл/кінець блоку і
наступний непорожній символ — велика літера, цифра, відкривна лапка або
тире (§10.1). Перед пошуком меж захищаються від хибного розпізнавання:

- десяткові числа `\\d+[.,]\\d+`;
- ініціали перед прізвищем і пари ініціалів (максимум 2 поспіль);
- версіонований список скорочень `ABBREVIATIONS` (`ABBREVIATIONS_VERSION`).

Ідея захисту — та сама, що в `parser/paragraph_analyzer._ABBR_RE` і
`_DECIMAL_NUM_RE` (заміна крапки на службовий символ перед пошуком меж), але
перенесена в спільний модуль, а не скопійована: версіонований список тут
свій, вужчий (14 позицій, без "грн", "млн", "кг" тощо — вони НЕ входять і
можуть ставати межею речення, `parser/paragraph_analyzer.py` не змінюється).

Межа блоку завершує речення, крім залишку останнього `AUTHOR_TEXT`-блоку
фізичної сторінки без термінальної пунктуації: такий залишок не стає
донором і позначається `is_page_boundary_fragment=True`
(`split_sentences_detailed`); завершені речення того ж блоку лишаються з
`False`. `split_sentences` (заморожена сигнатура — викликається з
`parser/searchdoc.py`) еквівалентна проєкції `split_sentences_detailed` на
`(start, end)` з `is_last_author_block_on_page=False` за замовчуванням.

`;` і `:` самі по собі речення не ділять.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WINDOW_MIN_WORDS = 6
WINDOW_MAX_WORDS = 10

_TERMINATORS = ".!?…"
_OPEN_QUOTE_CHARS = "«\"„“'"
_DASH_CHARS = "-–—"

# Символ-місцетримач замість захищеної крапки: не є термінатором речення і
# не зустрічається у вихідному тексті природно.
_SENTINEL = "\x00"

# Версіонований список скорочень (§10.1, §22 крок 4, «Числа»): рівно ці 14
# позицій, без розширення. Ширший `_ABBR_RE` з `parser/paragraph_analyzer.py`
# (там "грн", "млн", "кг" тощо) свідомо не переноситься й не наслідується.
ABBREVIATIONS_VERSION = "abbr-1"
ABBREVIATIONS: tuple[str, ...] = (
    "р.", "ст.", "ч.", "п.", "с.", "рис.", "табл.", "див.",
    "ім.", "проф.", "доц.", "д-р", "канд.", "наук.",
)

_ABBR_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(a) for a in sorted(ABBREVIATIONS, key=len, reverse=True)) + r")",
)

# Десяткові числа: одна крапка/кома між цифрами (§10.1, «Числа»/захисти).
_DECIMAL_NUM_RE = re.compile(r"\d+[.,]\d+")

# Ініціали перед прізвищем: від 1 до 2 ініціалів поспіль (максимум інших
# ініціалів перед прізвищем — 2, §22 крок 4 «Числа»), після — прізвище
# (велика літера, далі малі).
_INITIALS_RE = re.compile(
    r"(?:[А-ЯІЇЄҐA-Z]\.\s?){1,2}[А-ЯІЇЄҐA-Z][а-яіїєґa-z]+"
)


@dataclass(frozen=True)
class SentenceSpan:
    start: int  # зміщення в переданому тексті
    end: int
    is_page_boundary_fragment: bool


def _protect(text: str) -> str:
    """
    Повертає текст тієї самої довжини, де крапки всередині десяткових
    чисел, ініціалів і версіонованих скорочень замінені на `_SENTINEL` і
    тому не розпізнаються як термінатори речення. Довжина не змінюється —
    зміщення, знайдені на захищеному тексті, застосовні до оригіналу.
    """

    def _mask_dots(match: re.Match[str]) -> str:
        return match.group(0).replace(".", _SENTINEL)

    protected = _DECIMAL_NUM_RE.sub(_mask_dots, text)
    protected = _INITIALS_RE.sub(_mask_dots, protected)
    protected = _ABBR_RE.sub(_mask_dots, protected)
    return protected


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _find_completed_sentences(protected: str) -> tuple[list[tuple[int, int]], int]:
    """
    Проходить захищений текст і повертає завершені речення (межі в
    оригінальних координатах, бо довжина `protected` збігається з `text`)
    та позицію, з якої залишок тексту не має розпізнаної межі.
    """
    bounds: list[tuple[int, int]] = []
    start = 0
    i = 0
    n = len(protected)
    while i < n:
        ch = protected[i]
        if ch in _TERMINATORS:
            j = i + 1
            while j < n and protected[j] in _TERMINATORS:
                j += 1
            if j < n and protected[j].isspace():
                k = j
                while k < n and protected[k].isspace():
                    k += 1
                if k < n and (
                    protected[k].isupper() or protected[k].isdigit()
                    or protected[k] in _OPEN_QUOTE_CHARS or protected[k] in _DASH_CHARS
                ):
                    s, e = _trim(protected, start, j)
                    if s < e:
                        bounds.append((s, e))
                    start = k
                    i = k
                    continue
            elif j >= n:
                s, e = _trim(protected, start, j)
                if s < e:
                    bounds.append((s, e))
                start = j
                i = j
                continue
        i += 1
    return bounds, start


def split_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """
    Повертає впорядкований кортеж піввідкритих символьних меж `(start, end)`
    усіх речень у `text`, включно з незавершеним хвостом без термінальної
    пунктуації (він теж речення — питання "донор чи ні" вирішує викликач
    через `split_sentences_detailed`). Еквівалентна проєкції
    `split_sentences_detailed(text)` на `(start, end)`.
    """
    return tuple((s.start, s.end) for s in split_sentences_detailed(text))


def split_sentences_detailed(
    text: str, *, is_last_author_block_on_page: bool = False
) -> tuple[SentenceSpan, ...]:
    """
    Те саме, що `split_sentences`, але з прапорцем на кожному спані.
    Незавершений хвіст (без термінальної пунктуації в кінці блоку) отримує
    `is_page_boundary_fragment=True` лише якщо `is_last_author_block_on_page`
    істинний; усі завершені речення того ж блоку лишаються з `False`.
    """
    if not text:
        return ()

    protected = _protect(text)
    bounds, tail_start = _find_completed_sentences(protected)

    spans = [SentenceSpan(s, e, False) for s, e in bounds]

    s, e = _trim(text, tail_start, len(text))
    if s < e:
        spans.append(SentenceSpan(s, e, is_last_author_block_on_page))

    return tuple(spans)


def iter_word_windows(
    word_count: int,
    *,
    min_words: int = WINDOW_MIN_WORDS,
    max_words: int = WINDOW_MAX_WORDS,
) -> tuple[tuple[int, int], ...]:
    """
    Чисте перечислення півінтервалів індексів слів `(start, end)` довжиною
    від `min_words` до `max_words` включно, у порядку зростання `start`,
    потім зростання довжини (§13, п.1). Нічого не знає про текст чи бали.
    """
    if word_count < min_words:
        return ()

    windows: list[tuple[int, int]] = []
    for start in range(0, word_count):
        for length in range(min_words, max_words + 1):
            end = start + length
            if end > word_count:
                break
            windows.append((start, end))
    return tuple(windows)
