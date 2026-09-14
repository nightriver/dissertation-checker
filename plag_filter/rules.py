"""Правила режиму очищення звіту Plag: автор, дати, рішення.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §3, §4, §5, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.2, §9.2 (етап 1). Числа й порядок правил не
змінюються без правки плану.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import Literal
from urllib.parse import unquote, urlparse

from parser.text_forensics import normalize_mixed_homoglyphs
from plag_filter.types import (
    AuthorGuess,
    AuthorHit,
    DateInterval,
    Decision,
    PlagProject,
    PlagReport,
    Reason,
    SourceRow,
    SourceState,
)

# ---------------------------------------------------------------------------
# §3. Нормалізація тексту та пошук автора
# ---------------------------------------------------------------------------

_APOSTROPHES = "’ʼ`´"
_APOSTROPHE_RE = re.compile(f"[{_APOSTROPHES}]")
_HYPHEN_LINEBREAK_RE = re.compile(r"[-‐‑–]\n(.)")
_WHITESPACE_RE = re.compile(r"\s+")

_CAP = "А-ЯЁІЇЄҐ"
_LOWER = "а-яёіїєґ"
_SPACED_CAPS_RE = re.compile(
    rf"(?<![{_CAP}{_LOWER}])(?:[{_CAP}](?!\.)\s){{2,}}[{_CAP}](?!\.)(?![{_CAP}{_LOWER}])"
)

# Межа "не буква і не апостроф" — PLAN_PLAG_FILTER.md, §3.
_NOT_LETTER_BEFORE = r"(?<![^\W\d_])(?<!')"
_NOT_LETTER_AFTER = r"(?![^\W\d_])(?!')"


def _dehyphenate(match: re.Match[str]) -> str:
    letter = match.group(1)
    return letter if letter.islower() else match.group(0)


def normalize_for_author(text: str) -> str:
    """Нормалізувати текст для пошуку автора — PLAN_PLAG_FILTER.md, §3."""
    text = normalize_mixed_homoglyphs(text)
    text = _APOSTROPHE_RE.sub("'", text)
    text = _HYPHEN_LINEBREAK_RE.sub(_dehyphenate, text)
    text = text.replace("\n", " ")
    text = _SPACED_CAPS_RE.sub(lambda m: m.group(0).replace(" ", ""), text)
    text = text.casefold()
    text = _WHITESPACE_RE.sub(" ", text)
    return text


def _initials_letters(initials: str) -> list[str]:
    return [ch.casefold() for ch in initials if ch.isalpha()]


def author_key(surname: str, initials: str) -> str:
    """Ключ автора для порівняння перевірок — PLAN_PLAG_FILTER.md, §9."""
    surname_norm = normalize_for_author(surname)
    letters = _initials_letters(initials)
    initials_norm = "".join(f"{letter}." for letter in letters)
    return f"{surname_norm}|{initials_norm}"


def _initials_block(i1: str, i2: str | None) -> str:
    if i2 is None:
        return rf"{re.escape(i1)}\."
    return (
        rf"{re.escape(i1)}\."
        rf"(?!\s*(?!{re.escape(i2)})[^\W\d_]\.)"
        rf"(?:\s*{re.escape(i2)}\.)?"
    )


def _author_patterns(surname_norm: str, letters: list[str]) -> re.Pattern[str] | None:
    if not letters:
        return None
    surn = re.escape(surname_norm)
    i1 = letters[0]
    i2 = letters[1] if len(letters) > 1 else None
    block = _initials_block(i1, i2)

    pattern_a = rf"{_NOT_LETTER_BEFORE}{surn}{_NOT_LETTER_AFTER},?\s+{block}"
    pattern_b = rf"{block}\s+{_NOT_LETTER_BEFORE}{surn}{_NOT_LETTER_AFTER}"
    parts = [pattern_a, pattern_b]
    if i2 is not None:
        pattern_c = (
            rf"{_NOT_LETTER_BEFORE}{surn}{_NOT_LETTER_AFTER}"
            rf"\s+{re.escape(i1)}\S*\s+{re.escape(i2)}\S*"
        )
        parts.append(pattern_c)
    return re.compile("(?:" + ")|(?:".join(parts) + ")")


def find_author(pages: list[str], surname: str, initials: str) -> AuthorHit | None:
    """Знайти перший збіг прізвища й ініціалів автора — PLAN_PLAG_FILTER.md, §3."""
    letters = _initials_letters(initials)
    surname_norm = normalize_for_author(surname)
    pattern = _author_patterns(surname_norm, letters)
    if pattern is None:
        return None
    for page_index, page_text in enumerate(pages):
        normalized = normalize_for_author(page_text)
        match = pattern.search(normalized)
        if match is None:
            continue
        start = max(0, match.start() - 60)
        end = min(len(normalized), match.end() + 60)
        return AuthorHit(page=page_index, snippet=normalized[start:end])
    return None


# ---------------------------------------------------------------------------
# PLAN_PLAG_FILTER_V2.md, §8.2, §9.2 етап 1 — автор і рік із титулу
# ---------------------------------------------------------------------------

_RUKOPYSU_RE = re.compile(r"рукопису", re.IGNORECASE)
_ANCHOR_RE = re.compile(r"на\s+правах\s+рукопису", re.IGNORECASE)
_AUTHOR_WORD_RE = re.compile(r"[А-ЯҐЄІЇа-яґєії'’ʼ-]+")
_LOWER_UPPER_BOUNDARY_RE = re.compile(rf"(?<=[{_LOWER}])(?=[{_CAP}])")
_PATRONYMIC_SUFFIXES = (
    "ович",
    "евич",
    "йович",
    "івна",
    "ївна",
    "овна",
    "евна",
    "ич",
    "ична",
)


def derive_initials(given_name: str, patronymic: str) -> str:
    """Ініціали з імені та по батькові — PLAN_PLAG_FILTER_V2.md, §8.2."""
    letters = [part.strip()[0].upper() for part in (given_name, patronymic) if part.strip()]
    return "".join(f"{letter}." for letter in letters)


def _title_case_word(word: str) -> str:
    parts = word.split("-")
    return "-".join(part[:1].upper() + part[1:].lower() if part else part for part in parts)


def extract_author(title_text: str) -> AuthorGuess | None:
    """Витягти автора з тексту титулу — PLAN_PLAG_FILTER_V2.md, §8.2, §9.2 етап 1."""
    text = re.sub(r"\s+\d{1,4}\s+", " ", title_text)
    text = _RUKOPYSU_RE.sub(lambda m: m.group(0) + " ", text)
    text = _SPACED_CAPS_RE.sub(lambda m: m.group(0).replace(" ", ""), text)

    anchor = _ANCHOR_RE.search(text)
    if anchor is None:
        return None

    tail = _LOWER_UPPER_BOUNDARY_RE.sub(" ", text[anchor.end() :])
    matches = list(_AUTHOR_WORD_RE.finditer(tail))[:3]
    if len(matches) < 3:
        return None

    surname_raw, given_raw, patronymic_raw = (match.group(0) for match in matches)
    if not patronymic_raw.casefold().endswith(_PATRONYMIC_SUFFIXES):
        return None

    surname = _title_case_word(surname_raw)
    given_name = _title_case_word(given_raw)
    patronymic = _title_case_word(patronymic_raw)
    initials = derive_initials(given_name, patronymic)

    rest = tail[matches[2].end() :]
    confidence: Literal["confirmed", "single"] = (
        "confirmed" if find_author([rest], surname, initials) is not None else "single"
    )

    return AuthorGuess(
        surname=surname,
        given_name=given_name,
        patronymic=patronymic,
        initials=initials,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# §5. Датування з документа
# ---------------------------------------------------------------------------

_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d{4})$"), "year"),
    (re.compile(r"^(\d{4})-(\d{2})$"), "month"),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T.*)?$"), "day"),
    (re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"), "day"),
    (re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$"), "day"),
    (re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"), "day-dmy"),
)


def parse_date_value(value: str) -> DateInterval | None:
    """Розібрати значення дати з метаполя — PLAN_PLAG_FILTER.md, §5."""
    value = value.strip()
    for pattern, kind in _DATE_PATTERNS:
        match = pattern.match(value)
        if match is None:
            continue
        try:
            if kind == "year":
                year = int(match.group(1))
                if not 1900 <= year <= 2099:
                    return None
                return DateInterval(date(year, 1, 1), date(year, 12, 31), "year")
            if kind == "month":
                year, month = int(match.group(1)), int(match.group(2))
                if not 1900 <= year <= 2099:
                    return None
                last_day = calendar.monthrange(year, month)[1]
                return DateInterval(date(year, month, 1), date(year, month, last_day), "month")
            if kind == "day":
                year, month, day_ = int(match.group(1)), int(match.group(2)), int(match.group(3))
                if not 1900 <= year <= 2099:
                    return None
                d = date(year, month, day_)
                return DateInterval(d, d, "day")
            if kind == "day-dmy":
                day_, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                if not 1900 <= year <= 2099:
                    return None
                d = date(year, month, day_)
                return DateInterval(d, d, "day")
        except ValueError:
            return None
    return None


_META_FIELDS = ("citation_publication_date", "citation_date", "dc.date.issued")


def date_from_meta(meta: dict[str, str], jsonld: list[str]) -> tuple[DateInterval, str] | None:
    """Дата з метаполів HTML — PLAN_PLAG_FILTER.md, §5."""
    for field in _META_FIELDS:
        value = meta.get(field.casefold())
        if not value:
            continue
        interval = parse_date_value(value)
        if interval is not None:
            return interval, f"meta:{field}"

    if jsonld:
        try:
            data = json.loads(jsonld[0])
        except (json.JSONDecodeError, TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            candidates: list[dict] = [data]
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates.extend(item for item in graph if isinstance(item, dict))
            for candidate in candidates:
                value = candidate.get("datePublished")
                if not value:
                    continue
                interval = parse_date_value(str(value))
                if interval is not None:
                    return interval, "jsonld:datePublished"
    return None


_CITIES = (
    "київ|харків|львів|одеса|дніпро|дніпропетровськ|запоріжжя|ужгород|чернівці|"
    "вінниця|житомир|суми|полтава|тернопіль|івано-франківськ|луцьк|рівне|черкаси|"
    "чернігів|херсон|миколаїв|кропивницький|хмельницький|ірпінь|маріуполь|кременчук|"
    "киев|харьков|львов|одесса|днепропетровск|запорожье"
)

_PDF_ANCHORS: tuple[re.Pattern[str], ...] = (
    re.compile(r"№\s*\d+(?:\s*\(\d+\))?\s*[,.]\s*((?:19|20)\d\d)"),
    re.compile(r"вип(?:уск)?\.?\s*\d+\s*[,.]\s*((?:19|20)\d\d)"),
    re.compile(r"((?:19|20)\d\d)\s*\.?\s*[–—-]\s*(?:№|вип|т\.|том)"),
    re.compile(rf"(?:{_CITIES})\s*[,:–—-]\s*((?:19|20)\d\d)"),
)


def date_from_pdf_pages(pages: list[str]) -> tuple[DateInterval | None, str | None, bool]:
    """Дата з перших двох сторінок PDF-документа — PLAN_PLAG_FILTER.md, §5."""
    found: list[tuple[int, str, int]] = []
    for index, text in enumerate(pages[:2]):
        normalized = text.casefold()
        for pattern in _PDF_ANCHORS:
            for match in pattern.finditer(normalized):
                year = int(match.group(1))
                found.append((year, match.group(0), index + 1))

    years = {year for year, _fragment, _page in found}
    if not years:
        return None, None, False
    if len(years) > 1:
        return None, None, True
    year, fragment, page_number = found[0]
    interval = DateInterval(date(year, 1, 1), date(year, 12, 31), "year")
    basis = f"pdf:{fragment} (стор. {page_number})"
    return interval, basis, False


_TITLE_YEAR_RE = re.compile(rf"(?:{_CITIES})\s*[–—-]\s*((?:19|20)\d\d)")


def extract_title_year(title_text: str) -> int | None:
    """Рік дисертації з тексту титулу — PLAN_PLAG_FILTER_V2.md, §8.2, §9.2 етап 1."""
    match = _TITLE_YEAR_RE.search(title_text.casefold())
    if match is None:
        return None
    return int(match.group(1))


_YEAR_IN_TEXT_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def url_year_hint(url: str) -> int | None:
    """Найбільший рік у декодованих шляху та запиті адреси — PLAN_PLAG_FILTER.md, §5."""
    parsed = urlparse(url)
    text = f"{unquote(parsed.path)} {unquote(parsed.query)}"
    years = [int(match) for match in _YEAR_IN_TEXT_RE.findall(text)]
    return max(years) if years else None


# ---------------------------------------------------------------------------
# §4. Рішення
# ---------------------------------------------------------------------------


def decide(row: SourceRow, state: SourceState, project: PlagProject) -> tuple[Decision, Reason]:
    """Одне рішення для джерела за порядком правил — PLAN_PLAG_FILTER.md, §4."""
    if state.manual is not None:
        return state.manual, ("manual_keep" if state.manual == "keep" else "manual_exclude")

    if row.percent is not None and row.percent < 0.1:
        return "exclude", "below_threshold"

    check = state.check
    if check is None:
        return "disputed", "unchecked"

    if check.checked_for != author_key(project.surname, project.initials):
        return "disputed", "unchecked"

    if not project.confirmed:
        return "disputed", "unconfirmed"

    if check.author_hit is not None:
        return "exclude", "own_work"

    if check.error is not None:
        return "disputed", "unavailable"

    if check.date_conflict:
        return "disputed", "date_conflict"

    if check.doc_date is None:
        return "disputed", "date_unknown"

    year = project.year
    if year is None:
        return "disputed", "same_year"

    if check.doc_date.start > date(year, 12, 31):
        return "exclude", "later"

    if check.doc_date.end < date(year, 1, 1):
        return "keep", "earlier"

    return "disputed", "same_year"


def recompute(project: PlagProject, report: PlagReport) -> None:
    """Перерахувати рішення всіх джерел проєкту — PLAN_PLAG_FILTER.md, §4, §9."""
    for number, state in project.states.items():
        row = report.rows.get(number)
        if row is None:
            continue
        decision, reason = decide(row, state, project)
        state.decision = decision
        state.reason = reason


def order_numbers(
    project: PlagProject, report: PlagReport, key: Literal["width", "longest", "number"]
) -> list[int]:
    """Порядок номерів для перегляду — PLAN_PLAG_FILTER.md, §8, §9."""
    numbers = list(project.states.keys())
    if key == "number":
        return sorted(numbers)
    metric = report.highlight_width if key == "width" else report.longest_run
    return sorted(numbers, key=lambda number: (-metric.get(number, 0.0), number))


def top20_share(project: PlagProject, report: PlagReport) -> float | None:
    """Частка довжини виділень у топ-20 серед джерел, що залишилися — §8, §9."""
    widths: list[float] = []
    for number, state in project.states.items():
        row = report.rows.get(number)
        if row is None or state.decision == "exclude":
            continue
        if row.percent is not None and row.percent < 0.1:
            continue
        widths.append(report.highlight_width.get(number, 0.0))

    total = sum(widths)
    if total == 0:
        return None
    widths.sort(reverse=True)
    return sum(widths[:20]) / total
