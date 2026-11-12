"""Джерела за адресами й `PlagReport` зі звіту нового формату та дисертації —
PLAN_PLAG_FILTER_V4.md, §6.3, §7 етап 3.

Один документ Plag показує багатьма записами, тому джерело тут — адреса:
усі записи з однаковим ключем адреси зливаються. Знайдені в дисертації
шматки одного джерела зливаються в острови; острів, коротший за
`MIN_ISLAND_WORDS` слів, — дрібний збіг і відкидається в лічильник.

Результат — звичайний `PlagReport`, тож перевірка (`checker`), правила
(`rules`) і проєкт (`project`) працюють без змін. Аркуші звіту в ньому — це
аркуші дисертації.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from plag_filter.types import BodyEvent, PlagReport, SourceRow
from plag_filter.v4.align import DissText, Hit, load_dissertation, locate_entry
from plag_filter.v4.report import PlagEntry, V4Report, parse_v4_report

# Числа — PLAN_PLAG_FILTER_V4.md, §5.
MIN_TRANSLATED_SENTENCES = 7
ISLAND_GAP_CHARS = 40
MIN_ISLAND_WORDS = 8

_HOW = ("unique", "fragment", "nearest", "first")
_SECTION_RANK = {"main": 0, "translated": 1}


@dataclass(frozen=True)
class Island:
    """Злиті шматки одного джерела в потоці дисертації."""

    start: int
    end: int  # не включно
    words: int
    pages: tuple[int, ...]


@dataclass(frozen=True)
class V4Counters:
    """Лічильники відкинутого й знайденого — видимі завжди, §4."""

    main_entries: int
    translated_entries: int
    translated_below: int
    short_matches: int
    runs_total: int
    runs_located: int
    runs_by_how: dict[str, int]
    runs_missing: int
    small_islands: int
    small_island_words: int
    sources: int


@dataclass(frozen=True)
class V4Info:
    """Дані нового формату, яких немає в `PlagReport` старого формату."""

    report: V4Report
    diss: DissText
    islands: dict[int, tuple[Island, ...]]
    entries_by_number: dict[int, tuple[PlagEntry, ...]]
    counters: V4Counters


def address_key(url: str) -> str:
    """Ключ адреси: без схеми й фрагмента, хост у нижньому регістрі, без
    кінцевої `/` шляху — §6.3, правило 2."""
    parsed = urlparse(url.strip())
    key = parsed.netloc.lower() + parsed.path.rstrip("/")
    if parsed.query:
        key += "?" + parsed.query
    return key


def _islands(hits: list[Hit], diss: DissText) -> list[Island]:
    merged: list[list[int]] = []
    for hit in sorted(hits, key=lambda h: (h.start, h.end)):
        if merged and hit.start - merged[-1][1] <= ISLAND_GAP_CHARS:
            merged[-1][1] = max(merged[-1][1], hit.end)
        else:
            merged.append([hit.start, hit.end])
    islands: list[Island] = []
    for start, end in merged:
        words = sum(diss.word_start[start:end])
        first_page = diss.char_page[start]
        last_page = diss.char_page[end - 1]
        islands.append(Island(start, end, words, tuple(range(first_page, last_page + 1))))
    return islands


def build_from_parts(report: V4Report, diss: DissText, sha256: str) -> PlagReport:
    """Зібрати `PlagReport` з розібраного звіту й потоку дисертації — §6.3."""
    groups: dict[str, list[PlagEntry]] = {}
    translated_below = 0
    for entry in report.entries:
        if entry.section == "translated" and (entry.sentences or 0) < MIN_TRANSLATED_SENTENCES:
            translated_below += 1
            continue
        key = address_key(entry.url) if entry.url else f"{entry.section}:{entry.number}"
        groups.setdefault(key, []).append(entry)

    how_counts: Counter[str] = Counter({how: 0 for how in _HOW})
    runs_located = runs_missing = 0
    small_islands = small_words = 0

    candidates: list[tuple[tuple, str, list[PlagEntry], list[Island]]] = []
    for key, entries in groups.items():
        entries.sort(key=lambda e: (_SECTION_RANK[e.section], e.number))
        hits: list[Hit] = []
        for entry in entries:
            entry_hits, missing = locate_entry(entry, diss)
            hits.extend(entry_hits)
            runs_missing += missing
            for hit in entry_hits:
                how_counts[hit.how] += 1
        runs_located += len(hits)

        kept: list[Island] = []
        for island in _islands(hits, diss):
            if island.words < MIN_ISLAND_WORDS:
                small_islands += 1
                small_words += island.words
            else:
                kept.append(island)
        words = sum(island.words for island in kept)
        first = entries[0]
        order = (-words, _SECTION_RANK[first.section], first.number)
        candidates.append((order, key, entries, kept))

    candidates.sort(key=lambda item: item[0])

    rows: dict[int, SourceRow] = {}
    islands_by_number: dict[int, tuple[Island, ...]] = {}
    entries_by_number: dict[int, tuple[PlagEntry, ...]] = {}
    events: list[BodyEvent] = []
    highlight_width: dict[int, float] = {}
    longest_run: dict[int, float] = {}
    pages_by_number: dict[int, tuple[int, ...]] = {}
    numbers_by_page: dict[int, list[int]] = {}

    for number, (_order, key, entries, kept) in enumerate(candidates, start=1):
        first = entries[0]
        words = sum(island.words for island in kept)
        percent = words / report.total_words * 100 if report.total_words else 0.0
        has_main = any(entry.section == "main" for entry in entries)
        rows[number] = SourceRow(
            number=number,
            percent=percent,
            percent_text=f"{percent:.1f} %",
            label=first.domain if has_main else f"{first.domain} · переклад",
            urls=(first.url,),
            list_page=first.report_page,
            band=(0.0, 0.0),
            row_cuts=(),
            link_rects=(),
            source_id=hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        )
        islands_by_number[number] = tuple(kept)
        entries_by_number[number] = tuple(entries)
        if kept:
            highlight_width[number] = float(words)
            longest_run[number] = float(max(island.words for island in kept))
        pages: set[int] = set()
        for island in kept:
            events.append(
                BodyEvent(
                    page=island.pages[0],
                    number=number,
                    kind="highlight",
                    cuts=(),
                    width=float(island.words),
                )
            )
            pages.update(island.pages)
        if pages:
            pages_by_number[number] = tuple(sorted(pages))
            for page in pages:
                numbers_by_page.setdefault(page, []).append(number)

    main_entries = sum(1 for entry in report.entries if entry.section == "main")
    counters = V4Counters(
        main_entries=main_entries,
        translated_entries=len(report.entries) - main_entries,
        translated_below=translated_below,
        short_matches=report.short_count,
        runs_total=runs_located + runs_missing,
        runs_located=runs_located,
        runs_by_how=dict(how_counts),
        runs_missing=runs_missing,
        small_islands=small_islands,
        small_island_words=small_words,
        sources=len(rows),
    )
    info = V4Info(
        report=report,
        diss=diss,
        islands=islands_by_number,
        entries_by_number=entries_by_number,
        counters=counters,
    )
    events.sort(key=lambda event: (event.page, event.number))
    return PlagReport(
        sha256=sha256,
        page_count=diss.page_count,
        body_first=0,
        list_first=diss.page_count,
        rows=rows,
        events=tuple(events),
        pages_by_number=pages_by_number,
        numbers_by_page={page: tuple(sorted(nums)) for page, nums in numbers_by_page.items()},
        highlight_width=highlight_width,
        longest_run=longest_run,
        title_text=diss.title_text,
        kind="v4",
        v4=info,
    )


def build_report(report_data: bytes, diss_data: bytes) -> PlagReport:
    """Звіт нового формату + PDF дисертації → `PlagReport` — §6.3.

    `sha256` — від пари файлів: проєкт прив'язаний саме до цієї пари.
    """
    sha256 = hashlib.sha256(report_data + b"\0" + diss_data).hexdigest()
    return build_from_parts(parse_v4_report(report_data), load_dissertation(diss_data), sha256)
