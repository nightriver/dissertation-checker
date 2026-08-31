"""
search/normalization.py
Єдина нормалізація та токенізація тексту для режиму пошуку, з картою
символів `NormalizedText.origins` до вихідного `raw_text`. Специфікація —
PLAN_SEARCH.md, §7.

Конвеєр (§7): 1) Unicode NFKC; 2) нормалізація апострофів; 3) видалення
soft hyphen; 4) склейка буквеного переносу через дефіс лише в тексті аналізу;
5) нормалізація відомих гоміогліфів; 6) casefold — застосовується той, кому
потрібне порівняння (у самому `NormalizedText.text` регістр зберігається,
щоб можна було показати знахідку в природному вигляді); 7) токенізація.

Апострофна заміна навмисно виконується РАНІШЕ посимвольного NFKC для
символів з таблиці апострофів: NFKC у U+00B4 (ACUTE ACCENT) розкладає його
на два символи (пробіл + комбінований акцент), що суперечило б вимозі
"один вихідний символ на позицію апострофа". Для решти символів порядок
1→2 із §7 еквівалентний посимвольному проходу, бо апострофні кодпоінти не
чіпають одне одного.
"""

from __future__ import annotations

import re
import unicodedata

from search.types import CharOrigin, NormalizedText, RawSpan, SearchToken, SourceSpan

# Символ м'якого переносу (soft hyphen) видаляється без сліду в
# нормалізованому тексті; вихідний `raw_text` не змінюється (§7, п.3).
_SOFT_HYPHEN = "­"

# Символи-апострофи, що приводяться до прямого U+0027 (§7, п.2).
_APOSTROPHE_CHARS = frozenset("ʼ’‘´`")
_APOSTROPHE_TARGET = "'"

# Гоміогліфи: латиниця → кирилиця, рівно ці 20 пар (12 великих і 8 малих) і
# лише в цей бік (§10.1, «Числа»). Застосовуються лише всередині словесного
# токена, де є хоча б один кириличний символ (одиноке латинське слово
# гоміогліфом не стає).
HOMOGLYPHS_VERSION = "homoglyphs-1"
HOMOGLYPH_MAP: dict[str, str] = {
    "A": "А", "a": "а",
    "B": "В",
    "C": "С", "c": "с",
    "E": "Е", "e": "е",
    "H": "Н",
    "I": "І", "i": "і",
    "K": "К",
    "M": "М",
    "O": "О", "o": "о",
    "P": "Р", "p": "р",
    "T": "Т",
    "X": "Х", "x": "х",
    "y": "у",
}

_CYRILLIC_RE = re.compile(r"[а-яёіїєґА-ЯЁІЇЄҐ]")  # ru-data

# Слово: буквено-цифрові символи з можливими внутрішніми апострофом/дефісом
# (для української це не розбиває "розум'я", "будь-який" тощо). Число:
# послідовність цифр з десятковим/тисячним роздільником всередині.
WORD_TOKEN_RE = re.compile(
    r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*",
    re.UNICODE,
)


def normalize_text(raw_text: str) -> NormalizedText:
    """
    Повний конвеєр нормалізації §7 (кроки 1–5, 7 будує окремо `tokenize`):
    NFKC, апострофи, видалення soft hyphen, склейка переносу через дефіс,
    гоміогліфи — з побудовою `origins`, де кожен символ аналізу вказує на
    свій вихідний півінтервал `raw_text`. `raw_text` ніколи не змінюється.
    """
    if not raw_text:
        return NormalizedText(text="", origins=())

    if not _can_use_fast_primary_path(raw_text):
        return _normalize_text_slow(raw_text)
    chars0 = list(raw_text)
    origins0 = [CharOrigin(i, i + 1) for i in range(len(raw_text))]
    chars1, origins1 = _pass_join_hyphenation(chars0, origins0)
    chars2 = _pass_homoglyphs(chars1)
    return NormalizedText(text="".join(chars2), origins=tuple(origins1))


def normalize_for_matching(raw_text: str) -> str:
    """Нормалізація лише тексту без побудови карти походження (§7)."""

    if not raw_text:
        return ""
    if _can_use_fast_primary_path(raw_text):
        chars0 = list(raw_text)
    else:
        chars0 = _pass_nfkc_apostrophe_soft_hyphen_text(raw_text)
    chars1 = _pass_join_hyphenation_text(chars0)
    return "".join(_pass_homoglyphs(chars1))


def _can_use_fast_primary_path(raw_text: str) -> bool:
    return (
        unicodedata.is_normalized("NFKC", raw_text)
        and _SOFT_HYPHEN not in raw_text
        and not any(char in _APOSTROPHE_CHARS for char in raw_text)
    )


def _normalize_text_slow(raw_text: str) -> NormalizedText:
    """Повний посимвольний шлях нормалізації для перевірки fast path."""

    chars0, origins0 = _pass_nfkc_apostrophe_soft_hyphen(raw_text)
    chars1, origins1 = _pass_join_hyphenation(chars0, origins0)
    chars2 = _pass_homoglyphs(chars1)
    return NormalizedText(text="".join(chars2), origins=tuple(origins1))


def _pass_nfkc_apostrophe_soft_hyphen(
    raw_text: str,
) -> tuple[list[str], list[CharOrigin]]:
    """
    Кроки 1–3 §7: NFKC (посимвольно), апострофи, soft hyphen.

    Видалений символ (soft hyphen, або порожній результат NFKC) не створює
    символа аналізу, але карта `origins` лишається суцільною: його вихідний
    півінтервал поглинається сусіднім випущеним символом — спершу
    приєднується "вперед" до наступного випущеного символу (розширенням
    `raw_start` назад), а якщо це хвіст рядка без наступного символу — до
    попереднього випущеного символу (розширенням його `raw_end` вперед).
    """
    chars: list[str] = []
    origins: list[CharOrigin] = []
    pending_start: int | None = None
    for i, ch in enumerate(raw_text):
        if ch == _SOFT_HYPHEN:
            if pending_start is None:
                pending_start = i
            continue
        if ch in _APOSTROPHE_CHARS:
            piece = _APOSTROPHE_TARGET
        else:
            piece = unicodedata.normalize("NFKC", ch)
        if not piece:
            if pending_start is None:
                pending_start = i
            continue
        raw_start = pending_start if pending_start is not None else i
        pending_start = None
        origin = CharOrigin(raw_start=raw_start, raw_end=i + 1)
        for out_ch in piece:
            chars.append(out_ch)
            origins.append(origin)
    if pending_start is not None and origins:
        last = origins[-1]
        extended = CharOrigin(raw_start=last.raw_start, raw_end=len(raw_text))
        idx = len(origins) - 1
        while idx >= 0 and origins[idx] is last:
            origins[idx] = extended
            idx -= 1
    return chars, origins


def _pass_nfkc_apostrophe_soft_hyphen_text(raw_text: str) -> list[str]:
    """Текстовий відповідник первинного повного проходу нормалізації."""

    chars: list[str] = []
    for ch in raw_text:
        if ch == _SOFT_HYPHEN:
            continue
        if ch in _APOSTROPHE_CHARS:
            chars.append(_APOSTROPHE_TARGET)
        else:
            chars.extend(unicodedata.normalize("NFKC", ch))
    return chars


def _pass_join_hyphenation(
    chars0: list[str], origins0: list[CharOrigin]
) -> tuple[list[str], list[CharOrigin]]:
    """
    Крок 4 §7: склейка `<буква>-<перевід рядка><буква>`. Між дефісом і
    переводом рядка допускаються пробіли/табуляції, `\\r\\n` — один перевід
    рядка. Дефіс і перевід рядка не випускають жодного символу в аналіз;
    обидві половини слова зберігають власні вихідні інтервали.
    """
    chars1: list[str] = []
    origins1: list[CharOrigin] = []
    n = len(chars0)
    i = 0
    while i < n:
        ch = chars0[i]
        if ch == "-" and chars1 and chars1[-1].isalpha():
            j = i + 1
            while j < n and chars0[j] in (" ", "\t"):
                j += 1
            nl_len = 0
            if j < n and chars0[j] == "\r" and j + 1 < n and chars0[j + 1] == "\n":
                nl_len = 2
            elif j < n and chars0[j] == "\n":
                nl_len = 1
            if nl_len and (j + nl_len) < n and chars0[j + nl_len].isalpha():
                i = j + nl_len
                continue
        chars1.append(ch)
        origins1.append(origins0[i])
        i += 1
    return chars1, origins1


def _pass_join_hyphenation_text(chars0: list[str]) -> list[str]:
    """Текстовий відповідник склейки переносу без побудови origins."""

    chars1: list[str] = []
    n = len(chars0)
    i = 0
    while i < n:
        ch = chars0[i]
        if ch == "-" and chars1 and chars1[-1].isalpha():
            j = i + 1
            while j < n and chars0[j] in (" ", "\t"):
                j += 1
            nl_len = 0
            if j < n and chars0[j] == "\r" and j + 1 < n and chars0[j + 1] == "\n":
                nl_len = 2
            elif j < n and chars0[j] == "\n":
                nl_len = 1
            if nl_len and (j + nl_len) < n and chars0[j + nl_len].isalpha():
                i = j + nl_len
                continue
        chars1.append(ch)
        i += 1
    return chars1


def _pass_homoglyphs(chars1: list[str]) -> list[str]:
    """
    Крок 5 §7: гоміогліфи латиниці в кирилицю всередині словесного токена,
    де є хоча б один кириличний символ. Заміна символьна 1:1, позиції й
    довжина не змінюються — `origins` лишається дійсним без перерахунку.
    """
    text1 = "".join(chars1)
    chars2 = list(chars1)
    for match in WORD_TOKEN_RE.finditer(text1):
        start, end = match.start(), match.end()
        word = text1[start:end]
        if not _CYRILLIC_RE.search(word):
            continue
        for idx in range(start, end):
            replacement = HOMOGLYPH_MAP.get(chars2[idx])
            if replacement is not None:
                chars2[idx] = replacement
    return chars2


def map_normalized_offsets(
    normalized: NormalizedText, start: int, end: int
) -> tuple[tuple[int, int], ...]:
    """
    Перетворює півінтервал `[start, end)` нормалізованого тексту на
    впорядкований список невічних (raw_start, raw_end) інтервалів вихідного
    тексту, об'єднуючи суміжні вихідні інтервали (§7).
    """
    if start < 0 or end > len(normalized.origins) or start >= end:
        raise ValueError("Некоректний нормалізований діапазон")

    origins = normalized.origins[start:end]
    parts: list[tuple[int, int]] = []
    cur_start, cur_end = origins[0].raw_start, origins[0].raw_end
    for origin in origins[1:]:
        if origin.raw_start <= cur_end:
            cur_end = max(cur_end, origin.raw_end)
        else:
            parts.append((cur_start, cur_end))
            cur_start, cur_end = origin.raw_start, origin.raw_end
    parts.append((cur_start, cur_end))
    return tuple(parts)


def map_normalized_span(
    normalized: NormalizedText,
    start: int,
    end: int,
    *,
    block_id: str,
    physical_page: int,
) -> SourceSpan:
    """Обгортає `map_normalized_offsets` у `SourceSpan` для конкретного блоку."""
    offsets = map_normalized_offsets(normalized, start, end)
    return SourceSpan(
        parts=tuple(RawSpan(block_id, physical_page, s, e) for s, e in offsets)
    )


def tokenize(raw_text: str, normalized: NormalizedText) -> tuple[SearchToken, ...]:
    """
    Токенізація нормалізованого тексту зі збереженням символьних інтервалів
    у нормалізованому та вихідному тексті. Слово — буквена або числова
    послідовність (`WORD_TOKEN_RE`); інші не пробільні символи — по
    одному символьні токени пунктуації.
    """
    text = normalized.text
    tokens: list[SearchToken] = []
    pos = 0
    for match in WORD_TOKEN_RE.finditer(text):
        if match.start() > pos:
            _append_punctuation_tokens(tokens, raw_text, normalized, text, pos, match.start())
        tokens.append(_make_token(raw_text, normalized, text, match.start(), match.end(), True))
        pos = match.end()
    if pos < len(text):
        _append_punctuation_tokens(tokens, raw_text, normalized, text, pos, len(text))
    return tuple(tokens)


def _append_punctuation_tokens(
    tokens: list[SearchToken],
    raw_text: str,
    normalized: NormalizedText,
    text: str,
    start: int,
    end: int,
) -> None:
    for i in range(start, end):
        if text[i].isspace():
            continue
        tokens.append(_make_token(raw_text, normalized, text, i, i + 1, False))


def _make_token(
    raw_text: str,
    normalized: NormalizedText,
    text: str,
    n_start: int,
    n_end: int,
    is_word: bool,
) -> SearchToken:
    raw_start = normalized.origins[n_start].raw_start
    raw_end = normalized.origins[n_end - 1].raw_end
    return SearchToken(
        raw=raw_text[raw_start:raw_end],
        normalized=text[n_start:n_end],
        raw_start=raw_start,
        raw_end=raw_end,
        normalized_start=n_start,
        normalized_end=n_end,
        is_word=is_word,
    )
