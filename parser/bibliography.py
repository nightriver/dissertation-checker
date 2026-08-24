"""
bibliography.py
Розбивка тексту на три зони (body / bibliography / after)
та парсинг багаторядкових бібліографічних записів.
"""

from __future__ import annotations
import re
import unicodedata

from parser.types import LineItem, MAX_SOURCE_NUM, is_toc_entry
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Константи — заголовки й стоп-слова
# ---------------------------------------------------------------------------

BIBLIO_HEADERS: list[str] = [
    "СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
    "СПИСОК ЛІТЕРАТУРИ",
    "СПИСОК ВИКОРИСТАНОЇ ЛІТЕРАТУРИ",
    "ВИКОРИСТАНІ ДЖЕРЕЛА",
    "БІБЛІОГРАФІЯ",
    "БІБЛІОГРАФІЧНИЙ СПИСОК",
    "REFERENCES",
    "LITERATURE",
]

STOP_WORDS: list[str] = [
    "СПИСОК ПУБЛІКАЦІЙ ЗДОБУВАЧА",
    "ДОДАТКИ",
    "ДОДАТОК",
    "АНОТАЦІЯ",
    "ABSTRACT",
]

# Мінімум записів, щоб зона вважалася справжнім списком літератури.
# Нижче цього — це запис у ЗМІСТі, колонтитул або згадка в тексті.
MIN_BIBLIO_ENTRIES = 3

# Наскільки вужчий (вкладений) кандидат може поступатися ширшому за кількістю
# записів і все одно вигравати. Ширша зона, що поглинула тіло документа,
# набирає трохи більше за рахунок нумерованих списків у тексті.
_CONTAINMENT_RATIO = 0.9

# Патерн початку нового бібліографічного запису.
# Дві чіткі гілки, без асиметричних опціональних дужок:
#   Гілка 1: "1. Текст..."  — group(1)=номер, group(2)=текст
#   Гілка 2: "[1] Текст..." — group(3)=номер, group(4)=текст
# Text after number is optional: PyMuPDF sometimes puts number on its own line.
# "11.\n\nБайкулатова..." → гілка 1 matches, group(2) empty, next lines become content.
_ENTRY_START = re.compile(
    r"^\s*(?:(\d+)\.\s*(.*)|(?:\[(\d+)\])\s*(.*))$"
)



def _is_valid_entry(num: int, text_part: str) -> bool:
    """
    True якщо (num, text_part) виглядають як початок бібліографічного запису.

    Правила:
      • num має бути в діапазоні 1..999 (більше — це рік або номер документа)
      • text_part порожній → номер на окремому рядку, прийнятно
      • text_part непорожній → перший символ має бути літерою (Unicode),
        НЕ цифрою.

    Це відкидає рядки-продовження типу:
        "09.2019)."       → text="2019)."  починається з цифри ✗
        "12.00.07. Назва" → text="00.07…"  починається з цифри ✗
        "01.09.2019"      → text="09.2019" починається з цифри ✗
    І приймає:
        "10.Адміністративна" → text="Адмін…" починається з літери ✓
        "11."                → text=""        порожній ✓
    """
    if not (1 <= num <= MAX_SOURCE_NUM):
        return False
    if not text_part:
        return True   # номер на окремому рядку
    return unicodedata.category(text_part[0]).startswith('L')


# ---------------------------------------------------------------------------
# Допоміжні функції — пошук меж зон
# ---------------------------------------------------------------------------

def _normalize(line: str) -> str:
    """Верхній регістр, стиснені пробіли — для порівняння."""
    return re.sub(r"\s+", " ", line.strip().upper())


def _is_biblio_header(line: str) -> bool:
    n = _normalize(line)
    return any(n == h or n.startswith(h) for h in BIBLIO_HEADERS)


def _is_stop_word(line: str) -> bool:
    n = _normalize(line)
    return any(n == s or n.startswith(s) for s in STOP_WORDS)


# ---------------------------------------------------------------------------
# Публічний API
# ---------------------------------------------------------------------------

@dataclass
class ZoneSplitResult:
    body: list[LineItem]       # зона 1 — основний текст
    bibliography: list[LineItem]  # зона 2 — список літератури
    after: list[LineItem]      # зона 3 — ігнорується
    biblio_header_line: str | None = None   # знайдений заголовок
    biblio_start_page: int | None = None    # сторінка початку (PDF)
    found_automatically: bool = True


class BibliographyNotFoundError(Exception):
    pass


def _zone_end(lines: list[LineItem], start: int) -> int:
    """Індекс першого стоп-слова після start, або кінець документа."""
    for i in range(start + 1, len(lines)):
        if _is_stop_word(lines[i]["line"]):
            return i
    return len(lines)


def _select_biblio_header(lines: list[LineItem]) -> int:
    """
    Обирає рядок, з якого починається справжній список літератури.

    Раніше бралося просто ОСТАННЄ входження будь-якого заголовка. На реальних
    дисертаціях це давало грубі помилки: якщо після українського списку йде
    англомовна анотація зі своїм «REFERENCES», перемагав перекладений список,
    а справжній потрапляв у зону body — і всі метрики рахувалися не за тим
    списком.

    Правила відбору:
      1. Кандидат — будь-який рядок-заголовок, який НЕ є записом ЗМІСТу
         («СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ ...... 185»).
      2. Зона кандидата — від нього до першого стоп-слова (як і раніше).
      3. Оцінка кандидата — скільки записів парситься в його зоні.
      4. Кандидати, що не набрали MIN_BIBLIO_ENTRIES, відкидаються.
      5. Якщо зона кандидата A містить кандидата B і B набрав майже стільки ж
         (>= _CONTAINMENT_RATIO), перемагає вужчий B: так відсікається запис
         у ЗМІСТі, зона якого поглинула і тіло, і справжній список.
      6. Переможець — максимум за кількістю записів; за рівності — найраніший.
      7. Якщо жоден кандидат не набрав порогу — відкат до старої поведінки
         (останній кандидат), щоб UI показав звичне «не виявлено
         пронумерованих джерел», а не «список не знайдено».

    Зона НЕ обмежується наступним кандидатом навмисно: у PDF заголовок списку
    часто повторюється колонтитулом на кожній сторінці бібліографії, і саме
    завдяки необмеженій зоні найраніше входження отримує найбільшу оцінку.
    """
    candidates = [
        i for i, item in enumerate(lines)
        if _is_biblio_header(item["line"]) and not is_toc_entry(item["line"])
    ]
    if not candidates:
        return -1

    scored: list[tuple[int, int, int]] = []   # (idx, end, score)
    for idx in candidates:
        end = _zone_end(lines, idx)
        score = len(parse_bibliography(lines[idx:end]))
        scored.append((idx, end, score))

    qualified = [c for c in scored if c[2] >= MIN_BIBLIO_ENTRIES]
    if not qualified:
        return candidates[-1]

    # Правило вкладеності: вужчий кандидат усередині ширшого виграє,
    # якщо втрачає небагато записів.
    dropped: set[int] = set()
    for idx_a, end_a, score_a in qualified:
        for idx_b, _end_b, score_b in qualified:
            if idx_a < idx_b < end_a and score_b >= score_a * _CONTAINMENT_RATIO:
                dropped.add(idx_a)
                break

    remaining = [c for c in qualified if c[0] not in dropped] or qualified

    # Максимум за кількістю записів; за рівності — найраніший.
    best = max(remaining, key=lambda c: (c[2], -c[0]))
    return best[0]


def split_zones(lines: list[LineItem]) -> ZoneSplitResult:
    """
    Ділить список рядків на три зони: body / bibliography / after.

    lines: список {"line": str, "page": int | None}
    """
    biblio_start = _select_biblio_header(lines)

    if biblio_start < 0:
        raise BibliographyNotFoundError(
            "Список літератури не знайдено автоматично. "
            "Вкажіть розташування вручну."
        )

    biblio_end = _zone_end(lines, biblio_start)

    return ZoneSplitResult(
        body=lines[:biblio_start],
        bibliography=lines[biblio_start:biblio_end],
        after=lines[biblio_end:],
        biblio_header_line=lines[biblio_start]["line"].strip(),
        biblio_start_page=lines[biblio_start].get("page"),
        found_automatically=True,
    )


def split_zones_manual(
    lines: list[LineItem],
    header_text: str,
    start_page: int | None = None,
) -> ZoneSplitResult:
    """
    Ручний режим: шукаємо перший рядок, що містить header_text (без урахування
    регістру). Якщо задано start_page — шукаємо тільки на цій сторінці й далі.
    """
    header_norm = header_text.strip().upper()

    biblio_start: int | None = None
    for i, item in enumerate(lines):
        if start_page is not None and (item.get("page") or 0) < start_page:
            continue
        if header_norm in _normalize(item["line"]):
            biblio_start = i
            break

    if biblio_start is None:
        raise BibliographyNotFoundError(
            f"Рядок «{header_text}» не знайдено в документі."
        )

    biblio_end = len(lines)
    for i in range(biblio_start + 1, len(lines)):
        if _is_stop_word(lines[i]["line"]):
            biblio_end = i
            break

    return ZoneSplitResult(
        body=lines[:biblio_start],
        bibliography=lines[biblio_start:biblio_end],
        after=lines[biblio_end:],
        biblio_header_line=lines[biblio_start]["line"].strip(),
        biblio_start_page=lines[biblio_start].get("page"),
        found_automatically=False,
    )


def parse_bibliography(bibliography_lines: list[LineItem]) -> dict[int, str]:
    """
    Парсить зону bibliography → словник {номер: повний_текст}.
    Підтримує багаторядкові записи: рядки без патерну початку
    приєднуються до попереднього запису.

    Повертає порожній словник, якщо жодного запису не знайдено.
    """
    entries: dict[int, str] = {}
    current_num: int | None = None
    current_parts: list[str] = []

    def _flush():
        if current_num is not None and current_parts:
            entries[current_num] = " ".join(current_parts)

    for item in bibliography_lines:
        line = item["line"]
        m = _ENTRY_START.match(line)
        if m:
            # Гілка 1: "N. текст" → group(1), group(2)
            # Гілка 2: "[N] текст" → group(3), group(4)
            num_str = m.group(1) if m.group(1) is not None else m.group(3)
            text_part = (m.group(2) if m.group(1) is not None else m.group(4)) or ""
            text_part = text_part.strip()

            if _is_valid_entry(int(num_str), text_part):
                _flush()
                current_num = int(num_str)
                # Number may be alone on line; text starts on next line
                current_parts = [text_part] if text_part else []
            else:
                # Схоже на запис, але не валідне — продовження попереднього
                if current_num is not None:
                    stripped = line.strip()
                    if stripped:
                        current_parts.append(stripped)
        else:
            # Продовження попереднього запису (або заголовок зони — ігноруємо)
            if current_num is not None:
                stripped = line.strip()
                if stripped:
                    current_parts.append(stripped)

    _flush()
    return _drop_isolated_outliers(entries)


# Наскільки далеко за основною послідовністю має стояти номер, щоб узагалі
# розглядатися як випадковий. Дрібні дірки в нумерації (запис, який PyMuPDF
# розірвав) дають розрив у кілька одиниць — їх чіпати не можна.
_OUTLIER_MIN_GAP = 20


def _drop_isolated_outliers(entries: dict[int, str]) -> dict[int, str]:
    """
    Прибирає поодинокі «записи», номер яких випав далеко за межі списку.

    Фрагмент URL або числа в тексті іноді дає рядок на кшталт «457.» посеред
    списку з 166 джерел — і в результатах з'являється неіснуюче джерело №457,
    а разом із ним хибні «фантомні посилання».

    Видаляємо номер, лише якщо виконано ОБИДВІ умови:
      • він далі ніж на _OUTLIER_MIN_GAP за суцільним прогоном від початку;
      • у нього немає сусіда (num-1 чи num+1) серед розібраних записів.
    Тобто справжні шматки списку після дірки залишаються недоторканими.
    """
    if len(entries) < 3:
        return entries

    nums = sorted(entries)
    run_end = nums[0]
    for n in nums[1:]:
        if n == run_end + 1:
            run_end = n
        else:
            break

    result: dict[int, str] = {}
    for n in nums:
        far_out = n > run_end + _OUTLIER_MIN_GAP
        has_neighbour = (n - 1) in entries or (n + 1) in entries
        if far_out and not has_neighbour:
            continue
        result[n] = entries[n]
    return result
