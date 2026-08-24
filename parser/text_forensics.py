"""
text_forensics.py
Виявлення підміни кириличних літер візуально однаковими латинськими та
грецькими (гомогліфами) — типовий спосіб обійти пошук збігів.

Сканується ВЕСЬ документ, включно з бібліографією: підміна в списку
літератури ламає пошук самих джерел.

Модуль — чисті функції над рядками: ані PDF, ані DOCX для тестів не потрібні.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from parser.types import LineItem, Severity


# ---------------------------------------------------------------------------
# Таблиця підмін: латинська/грецька літера → кирилична, на яку вона схожа
# ---------------------------------------------------------------------------

_LATIN_TO_CYRILLIC: dict[str, str] = {
    # нижній регістр
    "a": "а", "e": "е", "i": "і", "o": "о", "p": "р",
    "c": "с", "y": "у", "x": "х", "ï": "ї",   # ï
    "j": "ј", "s": "ѕ",
    # верхній регістр
    "A": "А", "B": "В", "E": "Е", "I": "І", "K": "К",
    "M": "М", "H": "Н", "O": "О", "P": "Р", "C": "С",
    "T": "Т", "Y": "У", "X": "Х", "Ï": "Ї",   # Ï
    "J": "Ј", "S": "Ѕ",
}

# Грецькі двійники: трапляються рідше, коштують нуль.
# Мала «ν» до таблиці НЕ входить, хоч і згадана серед грецьких двійників:
# візуально вона збігається з латинською «v», а в кирилиці відповідника не має,
# тож «відновлений» варіант був би вигадкою.
_GREEK_TO_CYRILLIC: dict[str, str] = {
    "ο": "о",  # ο
    "α": "а",  # α
    "ρ": "р",  # ρ
    "Α": "А",  # Α
    "Β": "В",  # Β
    "Ε": "Е",  # Ε
    "Ι": "І",  # Ι
    "Κ": "К",  # Κ
    "Μ": "М",  # Μ
    "Ν": "Н",  # Ν
    "Ο": "О",  # Ο
    "Ρ": "Р",  # Ρ
    "Τ": "Т",  # Τ
    "Χ": "Х",  # Χ
}

# Спільна таблиця: обидві системи письма — «чужі» щодо кирилиці.
HOMOGLYPHS: dict[str, str] = {**_LATIN_TO_CYRILLIC, **_GREEK_TO_CYRILLIC}

_CYRILLIC_RE = re.compile(r'[а-яА-ЯёЁіІїЇєЄґҐјЈѕЅ]')
_FOREIGN_LETTER_RE = re.compile(r'[A-Za-zÀ-ɏͰ-Ͽ]')

# Токен — послідовність літер, дефісів та апострофів.
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'’ʼ][^\W\d_]+)*", re.UNICODE)

# Розбиття токена по дефісах і апострофах ПЕРЕД аналізом — саме воно знімає
# майже всі хибні спрацювання: «IT-технології» розпадається на «IT» (суцільна
# латиниця) і «технології» (суцільна кирилиця), жодне з них не змішане.
_SUBTOKEN_SPLIT_RE = re.compile(r"[-'’ʼ]")

# H1: частка кирилиці в підтокені, з якої слово вважається українським.
_MIN_CYRILLIC_SHARE = 0.5

# H2: латинські літери, що самі по собі є словом.
#   i, ï  — 🔴 завжди: латинські i/ï окремим словом в українському тексті
#           не трапляються, а і/ї — сполучник і прийменник.
#   y, o, a, e — лише коли підміну в документі вже доведено правилом H1.
#   x, c, p — виключені повністю: надто поширені математичні змінні.
_LONE_ALWAYS = {"i", "ï"}
_LONE_CONTEXTUAL = {"y", "o", "a", "e"}

# Скільки знахідок H1 потрібно, щоб увімкнулись контекстні літери H2.
_LONE_CONTEXT_THRESHOLD = 3

# За самотньою літерою не має йти символ переліку чи рівняння —
# це відсікає «a) b)» і «x = 5».
_LONE_TRAILING_STOP = {")", ".", "=", ","}

# Частка уражених слів, вище якої підміна виглядає тотальною.
_ENCODING_WORD_PCT = 5.0
# Частка уражених сторінок для того ж висновку (лише PDF).
_ENCODING_PAGE_PCT = 80.0


@dataclass
class HomoglyphHit:
    word: str                       # як у документі
    restored: str                   # як мало би бути
    rule: Literal["mixed", "lone"]
    severity: Severity
    page: Optional[int]
    line_index: int


@dataclass
class ForensicsResult:
    hits: list[HomoglyphHit] = field(default_factory=list)
    total_words: int = 0
    affected_pct: float = 0.0
    pages_affected: list[int] = field(default_factory=list)  # завжди [] для DOCX
    likely_encoding_issue: bool = False


def restore_word(word: str) -> str:
    """Замінює всі гомогліфи в слові на кириличні відповідники."""
    return "".join(HOMOGLYPHS.get(ch, ch) for ch in word)


def _is_mixed_hit(subtoken: str) -> bool:
    """
    H1 — змішане слово: підтокен містить літери двох систем письма, кирилиця
    становить ≥ 50 % літер, і ВСІ чужі літери є гомогліфами.

    Випадково набрати кириличне слово з латинською «i» всередині неможливо —
    розкладка так не перемикається.
    """
    cyrillic = _CYRILLIC_RE.findall(subtoken)
    foreign = _FOREIGN_LETTER_RE.findall(subtoken)
    if not cyrillic or not foreign:
        return False
    if len(cyrillic) / (len(cyrillic) + len(foreign)) < _MIN_CYRILLIC_SHARE:
        return False
    return all(ch in HOMOGLYPHS for ch in foreign)


def _lone_candidate(token: str, tail: str) -> bool:
    """
    H2 — самотня латинська літера-слово: окремий токен з однієї латинської
    літери. Слово з однієї літери на 100 % латинське, тож H1 його пропускає.

    Запобіжники: літера лише в нижньому регістрі; за нею не йде ')', '.',
    '=' або ',' — це відсікає переліки «a) b)» і рівняння «x = 5».

    Пробіли перед цим символом ігноруються: рівняння майже завжди пишуть
    саме як «i = 5», і перевірка суто наступного символа була б марною.
    """
    if len(token) != 1 or token not in HOMOGLYPHS:
        return False
    if token != token.lower():
        return False
    if tail.lstrip()[:1] in _LONE_TRAILING_STOP:
        return False
    return True


def _neighbours_are_cyrillic(tokens: list[str], index: int) -> bool:
    """Самотня літера має стояти між двома кириличними словами."""
    before = tokens[index - 1] if index > 0 else ""
    after = tokens[index + 1] if index + 1 < len(tokens) else ""
    return bool(_CYRILLIC_RE.search(before) and _CYRILLIC_RE.search(after))


def scan_text_forensics(lines: list[LineItem]) -> ForensicsResult:
    """
    Сканує документ на підміну символів.

    Повертає ForensicsResult; порожній документ не падає — total_words == 0,
    affected_pct == 0.
    """
    total_words = 0
    mixed_hits: list[HomoglyphHit] = []
    lone_candidates: list[tuple[HomoglyphHit, bool]] = []  # (знахідка, контекстна?)
    pages_seen: set[int] = set()
    pages_hit: set[int] = set()

    for line_index, item in enumerate(lines):
        text = item.get("line") or ""
        page = item.get("page")
        if page is not None:
            pages_seen.add(page)

        matches = list(_TOKEN_RE.finditer(text))
        tokens = [m.group(0) for m in matches]
        total_words += len(tokens)

        for token_index, match in enumerate(matches):
            token = match.group(0)
            tail = text[match.end():]

            # H1 — по підтокенах, розбитих за дефісами й апострофами.
            found_mixed = False
            for subtoken in _SUBTOKEN_SPLIT_RE.split(token):
                if _is_mixed_hit(subtoken):
                    found_mixed = True
                    break
            if found_mixed:
                mixed_hits.append(HomoglyphHit(
                    word=token,
                    restored=restore_word(token),
                    rule="mixed",
                    severity=Severity.PROOF,
                    page=page,
                    line_index=line_index,
                ))
                if page is not None:
                    pages_hit.add(page)
                continue

            # H2 — самотня латинська літера-слово.
            if not _lone_candidate(token, tail):
                continue
            if token not in _LONE_ALWAYS and token not in _LONE_CONTEXTUAL:
                continue
            if not _neighbours_are_cyrillic(tokens, token_index):
                continue

            lone_candidates.append((
                HomoglyphHit(
                    word=token,
                    restored=restore_word(token),
                    rule="lone",
                    severity=Severity.PROOF,
                    page=page,
                    line_index=line_index,
                ),
                token in _LONE_CONTEXTUAL,
            ))

    # Контекстні літери (y, o, a, e) вмикаються, лише коли H1 уже довело
    # підміну в документі. Поза цим контекстом вони дають хибні спрацювання
    # на змінних і латинських вставках.
    context_proven = len(mixed_hits) >= _LONE_CONTEXT_THRESHOLD
    lone_hits = [
        hit for hit, contextual in lone_candidates
        if not contextual or context_proven
    ]
    for hit in lone_hits:
        if hit.page is not None:
            pages_hit.add(hit.page)

    hits = sorted(mixed_hits + lone_hits, key=lambda h: (h.line_index, h.word))
    affected_pct = len(hits) / total_words * 100 if total_words else 0.0

    # Дискримінатор «атака чи поламаний шрифт» — розподіл, а не кількість.
    # Реальна атака точкова за визначенням: підмінюють лише те, що інакше
    # знайде пошук збігів.
    #
    # У DOCX номерів сторінок немає (page == None усюди), тому другий критерій
    # відпадає й лишається тільки частка уражених слів.
    docx_mode = not pages_seen
    if docx_mode:
        likely_encoding_issue = affected_pct > _ENCODING_WORD_PCT
        pages_affected: list[int] = []
    else:
        pages_pct = len(pages_hit) / len(pages_seen) * 100 if pages_seen else 0.0
        likely_encoding_issue = (
            affected_pct > _ENCODING_WORD_PCT and pages_pct > _ENCODING_PAGE_PCT
        )
        pages_affected = sorted(pages_hit)

    return ForensicsResult(
        hits=hits,
        total_words=total_words,
        affected_pct=affected_pct,
        pages_affected=pages_affected,
        likely_encoding_issue=likely_encoding_issue,
    )
