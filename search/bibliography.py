"""
search/bibliography.py
Записи бібліографії, заголовки, координати, посилання на джерела та
похідні ідентифікатори для режиму пошуку джерел.
Специфікація — PLAN_SEARCH.md, §12.5 і §12.6 (крок 6 таблиці §22).

Модуль нічого не парсить наново: розбір зони бібліографії робить
`parser.bibliography.parse_bibliography`, номерні посилання знаходить
`parser.citations.BRACKET_RE`, а вміст дужки розкриває
`parser.citations.expand_bracket`. Верхня межа номера джерела —
`parser.types.MAX_SOURCE_NUM`. Тут додаються лише координати, заголовок,
прізвища, рік і зв'язок посилання із записом.

Координати: `BRACKET_RE` і `expand_bracket` працюють на **нормалізованому**
тексті блоку, після чого зміщення повертаються у вихідні символи через
`search.normalization.map_normalized_span` (`NormalizedText.origins`).
Нормалізовані зміщення до `raw_text` напряму не застосовуються.

Мова запису тут НЕ визначається: це крок 7 (`search/language.py`). Усі
записи отримують `Language.UNKNOWN` і `language_evidence`
`"not_evaluated_until_step_7"`.

Правило №3 CLAUDE.md: нічого не глушиться мовчки. Кожна причина відсіву
з таблиці «Відмови» кроку 6 живе в лічильнику `BibliographyDiagnostics`,
і всі лічильники видно завжди, включно з нульовими.

Що свідомо НЕ будується: `kind == "footnote"` (§12.6, п.3). Надрядковий
номер розпізнається лише за геометрією і кеглем, а `SearchBlock` шрифтових
метрик не несе — по самому `SearchDocument` це правило невідтворюване.
`kind == "author_year"` зарезервований і в MVP не будується.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from parser.bibliography import parse_bibliography
from parser.citations import BRACKET_RE, expand_bracket
from parser.types import MAX_SOURCE_NUM
from search.normalization import map_normalized_offsets, map_normalized_span, normalize_text
from search.types import (
    BibliographyEntry,
    CitationMention,
    Confidence,
    Language,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SourceSpan,
    TextZone,
)

# ---------------------------------------------------------------------------
# Числа кроку 6
# ---------------------------------------------------------------------------

# §12.5, п.4: заголовок приймається лише в цих межах буквених токенів.
TITLE_MIN_LETTER_TOKENS = 3
TITLE_MAX_LETTER_TOKENS = 30

# Скільки шістнадцяткових символів SHA-256 лишається в ідентифікаторі.
ID_HEX_LENGTH = 16

# Вікно правдоподібного року видання: чотиризначне число поза ним роком
# не вважається (це може бути номер сторінки, тираж, номер справи).
YEAR_MIN = 1800
YEAR_MAX = 2100

# §12.6, п.2: між донором і посиланням не має бути жодного закінченого
# авторського речення з власним посиланням.
SAME_PARAGRAPH_MAX_INTERVENING = 0

# Заглушка мови до кроку 7.
LANGUAGE_NOT_EVALUATED = "not_evaluated_until_step_7"

# Причини відсіву (таблиця «Відмови» кроку 6). Порядок фіксований.
REJECTION_REASONS: tuple[str, ...] = (
    "title_out_of_bounds",
    "unresolved_source_number",
    "source_number_too_large",
    "surname_not_unique",
    "block_adjacency_only",
)

# Види зв'язку посилання із записом (`CitationMention.kind`).
KIND_NUMERIC = "numeric"
KIND_FOOTNOTE = "footnote"
KIND_AUTHOR_YEAR = "author_year"
KIND_SURNAME = "surname"


# ---------------------------------------------------------------------------
# Регулярні вирази
# ---------------------------------------------------------------------------

# Номер запису на початку рядка: "12." або "[12]".
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+\s*\.|\[\s*\d+\s*\])\s*")

# Буквений токен (цифри не рахуються): ним міряється довжина заголовка.
_LETTER_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)

# Лексер для розбору початкового блоку авторів: слово, розділовий знак
# або будь-який інший непробільний символ.
_LEX_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\S", re.UNICODE)

# Чотиризначне число, не оточене цифрами.
_FOUR_DIGITS_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")

# Будь-яке число всередині дужки посилання — лише для лічильника
# `source_number_too_large`; розкриття дужки робить `expand_bracket`.
_DIGITS_RE = re.compile(r"\d+")

# Пари лапок, якими заголовок виділяють явно (§12.5, п.2).
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("«", "»"),  # «...»
    ("„", "“"),  # „..."
    ("„", "”"),  # „..."
    ("“", "”"),  # "..."
    ('"', '"'),
)

# Роздільники, що завершують заголовок (§12.5, п.4): "//", "/", тире
# видавничого блоку. Назву журналу відділяє "//", рік шукається окремо.
_SEPARATOR_RE = re.compile(r"//|/|\s[—–−\-]\s")

# Символи, які зрізаються з країв заголовка.
_TITLE_TRIM = " \t\n\r.,:;—–−-«»„“”\"'"


# ---------------------------------------------------------------------------
# Діагностика
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BibliographyDiagnostics:
    """
    Лічильники відсіву кроку 6. Містять усі імена з `REJECTION_REASONS`
    завжди, включно з нульовими (CLAUDE.md, правило №3).
    """

    rejected_by_reason: tuple[tuple[str, int], ...]

    def count(self, reason: str) -> int:
        """Значення лічильника за іменем причини."""
        for name, value in self.rejected_by_reason:
            if name == reason:
                return value
        raise KeyError(reason)

    def as_dict(self) -> dict[str, int]:
        """Лічильники як звичайний словник (для звітів і тестів)."""
        return dict(self.rejected_by_reason)


def _new_counters() -> dict[str, int]:
    return {reason: 0 for reason in REJECTION_REASONS}


def _freeze_counters(counters: dict[str, int]) -> BibliographyDiagnostics:
    return BibliographyDiagnostics(
        rejected_by_reason=tuple((reason, counters[reason]) for reason in REJECTION_REASONS)
    )


# ---------------------------------------------------------------------------
# Ідентифікатори (§18.2)
# ---------------------------------------------------------------------------


def entry_id_for(document_sha256: str, ordinal: int | None, normalized_text: str) -> str:
    """
    `sha256(f"{document_sha256}|{ordinal}|{normalized_text}")[:16]`.

    Запис без номера підставляє рядок `none`. Вихідні координати в хеш не
    входять навмисно: перезбирання блоків не має міняти ідентифікатор
    запису (§18.2).
    """
    key = f"{document_sha256}|{'none' if ordinal is None else ordinal}|{normalized_text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_HEX_LENGTH]


def citation_id_for(document_sha256: str, block_id: str, raw_start: int, kind: str) -> str:
    """
    `sha256(f"{document_sha256}|{block_id}|{raw_start}|{kind}")[:16]`.

    На відміну від `entry_id`, залежить від координат: згадка прив'язана до
    конкретного місця в тексті.
    """
    key = f"{document_sha256}|{block_id}|{raw_start}|{kind}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_HEX_LENGTH]


def normalized_entry_text(raw_text: str) -> str:
    """
    Канонічна форма тексту запису для `entry_id`: нормалізація §7 плюс
    зведення будь-якої послідовності пробільних символів до одного пробілу.
    Регістр зберігається. Саме через це перенос запису на інший рядок або
    аркуш не міняє ідентифікатор.
    """
    return " ".join(normalize_text(raw_text).text.split())


# ---------------------------------------------------------------------------
# Внутрішні структури
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ZoneLine:
    """Один рядок зони BIBLIOGRAPHY з координатами всередині свого блоку."""

    block_index: int
    block_id: str
    physical_page: int
    raw_start: int
    raw_end: int
    text: str


@dataclass(frozen=True)
class _Part:
    """
    Частина запису в одному блоці та її зміщення в `raw_text` запису.

    `raw_text` зберігає вихідні переводи рядків, щоб його можна було точно
    відновити з `SourceSpan`. Пробільні символи зводить до одного пробілу
    лише `normalized_entry_text`, коли обчислює стабільний `entry_id`.
    """

    block_id: str
    physical_page: int
    block_start: int
    block_end: int
    text_start: int


def _slice_source(parts: tuple[_Part, ...], start: int, end: int) -> SourceSpan:
    """Півінтервал `[start, end)` у тексті запису → координати в блоках."""
    spans: list[RawSpan] = []
    for part in parts:
        length = part.block_end - part.block_start
        lo = max(start, part.text_start)
        hi = min(end, part.text_start + length)
        if lo >= hi:
            continue
        spans.append(
            RawSpan(
                block_id=part.block_id,
                physical_page=part.physical_page,
                raw_start=part.block_start + (lo - part.text_start),
                raw_end=part.block_start + (hi - part.text_start),
            )
        )
    return SourceSpan(parts=tuple(spans))


# ---------------------------------------------------------------------------
# §12.5 — записи бібліографії
# ---------------------------------------------------------------------------


def build_bibliography(document: SearchDocument) -> tuple[BibliographyEntry, ...]:
    """
    Записи бібліографії з зони `TextZone.BIBLIOGRAPHY` документа.

    Чиста функція від готового `SearchDocument`. Документ без зони
    бібліографії дає порожній кортеж без винятку.
    """
    entries, _ = build_bibliography_with_diagnostics(document)
    return entries


def build_bibliography_with_diagnostics(
    document: SearchDocument,
) -> tuple[tuple[BibliographyEntry, ...], BibliographyDiagnostics]:
    """`build_bibliography` плюс лічильники відсіву (правило №3 CLAUDE.md)."""
    counters = _new_counters()
    lines = _bibliography_lines(document)
    if not lines:
        return (), _freeze_counters(counters)

    parsed = parse_bibliography([{"line": line.text, "page": line.physical_page} for line in lines])
    if not parsed:
        return (), _freeze_counters(counters)

    blocks = {block.block_id: block for block in document.blocks}
    entries = tuple(
        _build_entry(document.document_sha256, blocks, lines, ordinal, first, last, counters)
        for ordinal, first, last in _locate_entries(lines, parsed)
    )
    return entries, _freeze_counters(counters)


def _bibliography_lines(document: SearchDocument) -> list[_ZoneLine]:
    """
    Рядки блоків, у яких є зона `BIBLIOGRAPHY`, у порядку документа.

    Блок ділиться на рядки за `\\n`: саме так `parser.searchdoc` склеює
    рядки аркуша в блок, і саме рядками мислить `parse_bibliography`.
    Колонтитул чи ЗМІСТ у зону не потрапляють: там виграє інша зона
    (`ZONE_PRIORITY`), і спана `BIBLIOGRAPHY` у блоці просто немає.
    """
    lines: list[_ZoneLine] = []
    for block in document.blocks:
        if not any(span.zone == TextZone.BIBLIOGRAPHY for span in block.zone_spans):
            continue
        offset = 0
        for piece in block.raw_text.split("\n"):
            lines.append(
                _ZoneLine(
                    block_index=block.block_index,
                    block_id=block.block_id,
                    physical_page=block.physical_page,
                    raw_start=offset,
                    raw_end=offset + len(piece),
                    text=piece,
                )
            )
            offset += len(piece) + 1
    return lines


def _locate_entries(
    lines: list[_ZoneLine], parsed: dict[int, str]
) -> list[tuple[int, int, int]]:
    """
    Прив'язує номери, які знайшов `parse_bibliography`, до рядків зони.

    `parse_bibliography` повертає лише `{номер: текст}` без координат, тому
    початок запису шукається за тим самим номером на початку рядка, а межа
    запису — це початок наступного знайденого. Номери проглядаються за
    зростанням і кожен шукається лише після попереднього: список літератури
    впорядкований, а повтор «12.» усередині URL початком запису не стає.

    Повертає трійки `(номер, індекс першого рядка, індекс останнього)`.
    """
    starts: list[tuple[int, int]] = []
    cursor = 0
    for ordinal in sorted(parsed):
        pattern = re.compile(rf"^\s*(?:{ordinal}\s*\.|\[\s*{ordinal}\s*\])")
        for index in range(cursor, len(lines)):
            if pattern.match(lines[index].text):
                starts.append((ordinal, index))
                cursor = index + 1
                break

    located: list[tuple[int, int, int]] = []
    for position, (ordinal, first) in enumerate(starts):
        last = starts[position + 1][1] - 1 if position + 1 < len(starts) else len(lines) - 1
        located.append((ordinal, first, last))
    return located


def _entry_parts(lines: list[_ZoneLine], first: int, last: int) -> tuple[_Part, ...]:
    """Координати запису: одна частина на кожен блок, якого він торкається."""
    parts: list[_Part] = []
    text_start = 0
    index = first
    while index <= last:
        block_id = lines[index].block_id
        end = index
        while end + 1 <= last and lines[end + 1].block_id == block_id:
            end += 1
        block_start = lines[index].raw_start
        block_end = lines[end].raw_end
        parts.append(
            _Part(
                block_id=block_id,
                physical_page=lines[index].physical_page,
                block_start=block_start,
                block_end=block_end,
                text_start=text_start,
            )
        )
        text_start += block_end - block_start
        index = end + 1
    return tuple(parts)


def _build_entry(
    document_sha256: str,
    blocks: dict[str, SearchBlock],
    lines: list[_ZoneLine],
    ordinal: int,
    first: int,
    last: int,
    counters: dict[str, int],
) -> BibliographyEntry:
    parts = _entry_parts(lines, first, last)
    raw_text = _parts_text(blocks, parts)
    normalized = normalize_text(raw_text)
    body_start = _body_start(normalized.text)
    title, title_span, title_confidence = _extract_title(normalized.text, body_start, counters)

    title_source: SourceSpan | None = None
    if title is not None and title_span is not None:
        title_source = _normalized_span_to_source(normalized, parts, *title_span)

    return BibliographyEntry(
        entry_id=entry_id_for(document_sha256, ordinal, normalized_entry_text(raw_text)),
        ordinal=ordinal,
        raw_text=raw_text,
        source=_slice_source(parts, 0, len(raw_text)),
        title=title,
        title_source=title_source,
        title_confidence=title_confidence,
        surnames=_extract_surnames(normalized.text[body_start:]),
        year=_extract_year(normalized.text[body_start:]),
        language=Language.UNKNOWN,
        language_evidence=LANGUAGE_NOT_EVALUATED,
    )


def _parts_text(blocks: dict[str, SearchBlock], parts: tuple[_Part, ...]) -> str:
    """Вихідний текст запису: рівно те, що дають координати `parts`."""
    return "".join(
        blocks[part.block_id].raw_text[part.block_start : part.block_end] for part in parts
    )


def _normalized_span_to_source(
    normalized, parts: tuple[_Part, ...], start: int, end: int
) -> SourceSpan:
    """Півінтервал нормалізованого тексту запису → координати в блоках."""
    spans: list[RawSpan] = []
    for raw_start, raw_end in map_normalized_offsets(normalized, start, end):
        spans.extend(_slice_source(parts, raw_start, raw_end).parts)
    return SourceSpan(parts=tuple(spans))


# ---------------------------------------------------------------------------
# §12.5 — заголовок
# ---------------------------------------------------------------------------


def _body_start(normalized_text: str) -> int:
    """Крок 1 §12.5: зміщення після номера запису на початку."""
    match = _LEADING_NUMBER_RE.match(normalized_text)
    return match.end() if match else 0


def _extract_title(
    normalized_text: str, body_start: int, counters: dict[str, int]
) -> tuple[str | None, tuple[int, int] | None, Confidence]:
    """
    Заголовок за §12.5. Виграє перше правило, що спрацювало:
    явні лапки → HIGH; початковий блок авторів з ініціалами → MEDIUM;
    чиста евристика «від початку до першого роздільника» → LOW.

    Заголовок поза межами `TITLE_MIN_LETTER_TOKENS`..`TITLE_MAX_LETTER_TOKENS`
    буквених токенів не приймається: запис лишається, заголовка немає
    (лічильник `title_out_of_bounds`).
    """
    body = normalized_text[body_start:]
    quoted = _find_quoted(body)
    if quoted is not None:
        start, end = quoted
        confidence = Confidence.HIGH
    else:
        authors_end = _author_block_end(body)
        confidence = Confidence.MEDIUM if authors_end else Confidence.LOW
        start = authors_end
        end = _first_separator(body, authors_end)

    start, end = _trim(body, start, end)
    if start >= end:
        counters["title_out_of_bounds"] += 1
        return None, None, Confidence.LOW

    candidate = body[start:end]
    tokens = len(_LETTER_TOKEN_RE.findall(candidate))
    if not TITLE_MIN_LETTER_TOKENS <= tokens <= TITLE_MAX_LETTER_TOKENS:
        counters["title_out_of_bounds"] += 1
        return None, None, Confidence.LOW

    title = " ".join(candidate.split())
    return title, (body_start + start, body_start + end), confidence


def _find_quoted(body: str) -> tuple[int, int] | None:
    """Перший фрагмент у явних лапках: межі тексту без самих лапок."""
    best: tuple[int, int] | None = None
    for opening, closing in _QUOTE_PAIRS:
        start = body.find(opening)
        if start < 0:
            continue
        end = body.find(closing, start + 1)
        if end < 0:
            continue
        if best is None or start < best[0]:
            best = (start + 1, end)
    return best


def _first_separator(body: str, from_pos: int) -> int:
    """
    Зміщення першого роздільника, що завершує заголовок (§12.5, п.4):
    `/`, `//`, тире видавничого блоку або правдоподібний рік.
    """
    end = len(body)
    match = _SEPARATOR_RE.search(body, from_pos)
    if match:
        end = match.start()
    for year_match in _FOUR_DIGITS_RE.finditer(body, from_pos):
        if YEAR_MIN <= int(year_match.group(1)) <= YEAR_MAX:
            end = min(end, year_match.start())
            break
    return end


def _trim(body: str, start: int, end: int) -> tuple[int, int]:
    """Зрізає пробіли, розділові знаки й лапки з країв кандидата."""
    while start < end and body[start] in _TITLE_TRIM:
        start += 1
    while end > start and body[end - 1] in _TITLE_TRIM:
        end -= 1
    return start, end


# ---------------------------------------------------------------------------
# §12.5 — початковий блок авторів і прізвища
# ---------------------------------------------------------------------------


def _lex(body: str) -> list[tuple[int, int, str]]:
    """Слова й одиничні символи на початку тексту, у порядку появи."""
    return [(m.start(), m.end(), m.group()) for m in _LEX_RE.finditer(body)]


def _is_initial(token: str) -> bool:
    return len(token) == 1 and token.isalpha() and token.isupper()


def _is_surname(token: str) -> bool:
    return len(token) > 1 and token[0].isalpha() and token[0].isupper()


def _match_author_unit(tokens: list[tuple[int, int, str]], index: int) -> tuple[int, str] | None:
    """
    Одна одиниця авторського блоку: «Прізвище І. І.», «Прізвище, І. І.»
    або «І. І. Прізвище». Повертає індекс наступного токена і прізвище.
    """
    position = index
    initials = 0
    while position + 1 < len(tokens) and _is_initial(tokens[position][2]) and tokens[position + 1][2] == ".":
        initials += 1
        position += 2
    if initials:
        if position < len(tokens) and _is_surname(tokens[position][2]):
            return position + 1, tokens[position][2]
        return None

    if not _is_surname(tokens[index][2]):
        return None
    position = index + 1
    if position < len(tokens) and tokens[position][2] == ",":
        position += 1
    trailing = 0
    while position + 1 < len(tokens) and _is_initial(tokens[position][2]) and tokens[position + 1][2] == ".":
        trailing += 1
        position += 2
    if not trailing:
        return None
    return position, tokens[index][2]


def _author_block(body: str) -> tuple[int, tuple[str, ...]]:
    """Довжина початкового блоку авторів і прізвища з нього."""
    tokens = _lex(body)
    end = 0
    surnames: list[str] = []
    index = 0
    while index < len(tokens):
        unit = _match_author_unit(tokens, index)
        if unit is None:
            break
        index, surname = unit
        end = tokens[index - 1][1]
        surnames.append(surname)
        if index < len(tokens) and tokens[index][2] in (",", ";"):
            index += 1
    return end, tuple(surnames)


def _author_block_end(body: str) -> int:
    return _author_block(body)[0]


def _extract_surnames(body: str) -> tuple[str, ...]:
    """
    Прізвища запису: початковий блок авторів плюс блок одразу після `/`
    (форма ДСТУ «Назва / І. І. Іваненко»). Ініціали до прізвищ не
    потрапляють. Порядок появи зберігається, повтори прибираються.
    """
    _, leading = _author_block(body)
    slash = body.find("/")
    after_slash: tuple[str, ...] = ()
    if slash >= 0:
        tail = body[slash + 1 :]
        after_slash = _author_block(tail.lstrip())[1]

    seen: dict[str, None] = {}
    for surname in (*leading, *after_slash):
        seen.setdefault(surname, None)
    return tuple(seen)


def _extract_year(body: str) -> int | None:
    """Перше правдоподібне чотиризначне число запису (`YEAR_MIN`..`YEAR_MAX`)."""
    for match in _FOUR_DIGITS_RE.finditer(body):
        value = int(match.group(1))
        if YEAR_MIN <= value <= YEAR_MAX:
            return value
    return None


# ---------------------------------------------------------------------------
# §12.6 — згадки джерел у тексті
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Donor:
    """Речення-донор блоку в координатах `raw_text` цього блоку."""

    donor_id: str
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class _Bracket:
    """Номерне посилання в блоці: нормалізовані та вихідні координати."""

    normalized_start: int
    normalized_end: int
    raw_start: int
    raw_end: int
    content: str


def build_citations(
    document: SearchDocument, entries: tuple[BibliographyEntry, ...]
) -> tuple[CitationMention, ...]:
    """
    Згадки джерел у тілі роботи, пов'язані з уже розібраними записами.

    Номер посилання розв'язується **через розібрану бібліографію**: номер,
    якого в `entries` немає, згадкою не стає. Документ без бібліографії дає
    порожній кортеж без винятку.
    """
    citations, _ = build_citations_with_diagnostics(document, entries)
    return citations


def build_citations_with_diagnostics(
    document: SearchDocument, entries: tuple[BibliographyEntry, ...]
) -> tuple[tuple[CitationMention, ...], BibliographyDiagnostics]:
    """`build_citations` плюс лічильники відсіву (правило №3 CLAUDE.md)."""
    counters = _new_counters()
    if not entries:
        return (), _freeze_counters(counters)

    by_ordinal = {entry.ordinal: entry for entry in entries if entry.ordinal is not None}
    donors = _donors_by_block(document)
    surname_index = _unique_surname_index(entries, counters)

    mentions: list[CitationMention] = []
    for block in document.blocks:
        block_donors = donors.get(block.block_id, ())
        if not block_donors:
            continue
        brackets = _block_brackets(block)
        mentions.extend(
            _numeric_mentions(document, block, block_donors, brackets, by_ordinal, counters)
        )
        mentions.extend(
            _surname_mentions(document, block, block_donors, surname_index)
        )
    return tuple(mentions), _freeze_counters(counters)


def _donors_by_block(document: SearchDocument) -> dict[str, tuple[_Donor, ...]]:
    """Речення-донори кроку 5, згруповані за блоком і впорядковані."""
    grouped: dict[str, list[_Donor]] = {}
    for donor in document.sentences:
        for part in donor.source.parts:
            grouped.setdefault(donor.block_id, []).append(
                _Donor(donor.donor_id, part.raw_start, part.raw_end)
            )
    return {
        block_id: tuple(sorted(items, key=lambda d: (d.raw_start, d.raw_end)))
        for block_id, items in grouped.items()
    }


def _block_brackets(block: SearchBlock) -> tuple[_Bracket, ...]:
    """
    Номерні посилання блоку: `BRACKET_RE` працює на нормалізованому тексті,
    координати повертаються у вихідні символи через `origins` (§12.6).
    """
    brackets: list[_Bracket] = []
    for match in BRACKET_RE.finditer(block.normalized.text):
        source = map_normalized_span(
            block.normalized,
            match.start(),
            match.end(),
            block_id=block.block_id,
            physical_page=block.physical_page,
        )
        brackets.append(
            _Bracket(
                normalized_start=match.start(),
                normalized_end=match.end(),
                raw_start=source.parts[0].raw_start,
                raw_end=source.parts[-1].raw_end,
                content=match.group(1),
            )
        )
    return tuple(brackets)


def _numeric_mentions(
    document: SearchDocument,
    block: SearchBlock,
    donors: tuple[_Donor, ...],
    brackets: tuple[_Bracket, ...],
    by_ordinal: dict[int | None, BibliographyEntry],
    counters: dict[str, int],
) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    for bracket in brackets:
        for digits in _DIGITS_RE.findall(bracket.content):
            if int(digits) > MAX_SOURCE_NUM:
                counters["source_number_too_large"] += 1

        numbers = sorted(expand_bracket(bracket.content))
        resolved = [by_ordinal[number] for number in numbers if number in by_ordinal]
        for number in numbers:
            if number not in by_ordinal:
                counters["unresolved_source_number"] += 1
        if not resolved:
            continue

        if not _linked_donors(donors, brackets, bracket):
            counters["block_adjacency_only"] += 1
            continue

        mentions.append(
            CitationMention(
                citation_id=citation_id_for(
                    document.document_sha256, block.block_id, bracket.raw_start, KIND_NUMERIC
                ),
                kind=KIND_NUMERIC,
                source=map_normalized_span(
                    block.normalized,
                    bracket.normalized_start,
                    bracket.normalized_end,
                    block_id=block.block_id,
                    physical_page=block.physical_page,
                ),
                entry_ids=tuple(entry.entry_id for entry in resolved),
                confidence=Confidence.HIGH,
            )
        )
    return mentions


def _linked_donors(
    donors: tuple[_Donor, ...], brackets: tuple[_Bracket, ...], bracket: _Bracket
) -> tuple[str, ...]:
    """
    Донори, для яких це посилання є доказом (§12.6, пп. 1–2).

    1. Речення, всередині якого стоїть посилання, — прямий доказ.
    2. Далі назад по тому самому абзаці: кожне попереднє речення теж
       спирається на це посилання, доки не трапиться закінчене речення з
       власним посиланням. Таке речення належить власному джерелу, тому не
       зв'язується з поточним; за `SAME_PARAGRAPH_MAX_INTERVENING = 0` воно
       також блокує всі попередні речення.

    Саме сусідство фізичних блоків доказом не є ніколи: донори беруться
    лише з того самого блоку.
    """
    linked: list[str] = []
    for donor in donors:
        if donor.raw_start <= bracket.raw_start and bracket.raw_end <= donor.raw_end:
            linked.append(donor.donor_id)
            break

    intervening = 0
    for donor in reversed(donors):
        if donor.raw_end > bracket.raw_start:
            continue
        if _has_own_bracket(donor, brackets):
            intervening += 1
            if intervening > SAME_PARAGRAPH_MAX_INTERVENING:
                break
            continue
        linked.append(donor.donor_id)
    return tuple(linked)


def _has_own_bracket(donor: _Donor, brackets: tuple[_Bracket, ...]) -> bool:
    return any(
        donor.raw_start <= bracket.raw_start and bracket.raw_end <= donor.raw_end
        for bracket in brackets
    )


def donor_ids_for_mention(
    document: SearchDocument, mention: CitationMention
) -> tuple[str, ...]:
    """
    Донори, на які поширюється доказ цієї згадки (§12.6, пп. 1–2).

    `CitationMention` навмисно не зберігає донорів: згадка — це місце в
    тексті, а не пара «донор + місце», інакше `citation_id` перестав би
    бути унікальним. Крок 10 бере зв'язок звідси.
    """
    if not mention.source.parts:
        return ()
    block_id = mention.source.parts[0].block_id
    block = next((b for b in document.blocks if b.block_id == block_id), None)
    if block is None:
        return ()
    donors = _donors_by_block(document).get(block_id, ())
    brackets = _block_brackets(block)
    raw_start = mention.source.parts[0].raw_start
    raw_end = mention.source.parts[-1].raw_end
    if mention.kind == KIND_SURNAME:
        return tuple(
            donor.donor_id
            for donor in donors
            if donor.raw_start <= raw_start and raw_end <= donor.raw_end
        )
    target = next(
        (b for b in brackets if b.raw_start == raw_start and b.raw_end == raw_end), None
    )
    if target is None:
        return ()
    return _linked_donors(donors, brackets, target)


def _surname_mentions(
    document: SearchDocument,
    block: SearchBlock,
    donors: tuple[_Donor, ...],
    index: dict[str, str],
) -> list[CitationMention]:
    """
    §12.6, п.4: прізвище, унікальне в розібраній бібліографії, присутнє і в
    доноровому реченні, і в записі, дає зв'язок рівня `MEDIUM`.

    Порівняння — точний збіг словоформи без урахування регістру: крок
    пошуку працює словоформами, `pymorphy3` заборонений (CLAUDE.md,
    правило №6).
    """
    if not index:
        return []

    mentions: list[CitationMention] = []
    for token in block.tokens:
        if not token.is_word:
            continue
        entry_id = index.get(token.normalized.casefold())
        if entry_id is None:
            continue
        if not any(
            donor.raw_start <= token.raw_start and token.raw_end <= donor.raw_end
            for donor in donors
        ):
            continue
        mentions.append(
            CitationMention(
                citation_id=citation_id_for(
                    document.document_sha256, block.block_id, token.raw_start, KIND_SURNAME
                ),
                kind=KIND_SURNAME,
                source=SourceSpan(
                    parts=(
                        RawSpan(
                            block_id=block.block_id,
                            physical_page=block.physical_page,
                            raw_start=token.raw_start,
                            raw_end=token.raw_end,
                        ),
                    )
                ),
                entry_ids=(entry_id,),
                confidence=Confidence.MEDIUM,
            )
        )
    return mentions


def _unique_surname_index(
    entries: tuple[BibliographyEntry, ...], counters: dict[str, int]
) -> dict[str, str]:
    """
    Прізвище → `entry_id` єдиного запису, де воно є. Прізвище, що
    трапляється більш ніж в одному записі, зв'язку не дає
    (лічильник `surname_not_unique`).
    """
    owners: dict[str, set[str]] = {}
    for entry in entries:
        for surname in entry.surnames:
            owners.setdefault(surname.casefold(), set()).add(entry.entry_id)

    index: dict[str, str] = {}
    for surname, entry_ids in owners.items():
        if len(entry_ids) == 1:
            index[surname] = next(iter(entry_ids))
        else:
            counters["surname_not_unique"] += 1
    return index
