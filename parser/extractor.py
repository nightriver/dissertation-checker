"""
extractor.py
Витяг тексту з PDF і DOCX у вигляді списку рядків.
Кожен елемент: {"line": str, "page": int | None}
"""

from __future__ import annotations
import re
import datetime
from typing import BinaryIO


MAX_FILE_SIZE = 30 * 1024 * 1024  # 30 МБ


class FileTooLargeError(Exception):
    pass


class ScannedPDFError(Exception):
    pass


class UnsupportedFormatError(Exception):
    pass


def _check_size(data: bytes) -> None:
    if len(data) > MAX_FILE_SIZE:
        raise FileTooLargeError("Файл завеликий. Максимальний розмір — 30 МБ.")


def extract_lines_from_pdf(data: bytes) -> list[dict]:
    """
    Повертає список {"line": str, "page": int} для кожного непорожнього
    візуального рядка документа. Сторінки нумеруються з 1.
    Якщо весь документ не містить тексту — кидає ScannedPDFError.
    """
    _check_size(data)

    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ImportError("Бібліотека PyMuPDF не встановлена.") from e

    result: list[dict] = []
    total_chars = 0

    with fitz.open(stream=data, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")
            total_chars += len(text.strip())
            for raw_line in text.splitlines():
                stripped = raw_line.rstrip()
                if stripped:
                    result.append({"line": stripped, "page": page_num})

    if total_chars == 0:
        raise ScannedPDFError(
            "Файл є скан-копією або захищеним PDF — текст недоступний."
        )

    return result


def extract_lines_from_docx(data: bytes) -> list[dict]:
    """
    Повертає список {"line": str, "page": None} для кожного непорожнього
    параграфа DOCX. Номер сторінки недоступний без рендерингу.
    """
    _check_size(data)

    import io

    try:
        from docx import Document
    except ImportError as e:
        raise ImportError("Бібліотека python-docx не встановлена.") from e

    doc = Document(io.BytesIO(data))
    result: list[dict] = []

    for para in doc.paragraphs:
        stripped = para.text.rstrip()
        if stripped:
            result.append({"line": stripped, "page": None})

    return result


def extract_lines(data: bytes, filename: str) -> list[dict]:
    """
    Диспетчер: визначає формат за розширенням та викликає відповідний екстрактор.
    filename використовується тільки для визначення розширення.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_lines_from_pdf(data)
    elif lower.endswith(".docx"):
        return extract_lines_from_docx(data)
    else:
        raise UnsupportedFormatError(
            f"Непідтримуваний формат файлу: «{filename}». "
            "Дозволено лише .pdf та .docx."
        )


# ---------------------------------------------------------------------------
# Якірні фрази для визначення року дисертації
# ---------------------------------------------------------------------------

# Рік після назви міста з тире: "Київ – 2023", "Харків — 2021"
_ANCHOR_CITY_YEAR = re.compile(
    r'(?:Київ|Харків|Львів|Одеса|Дніпро|Запоріжжя|Вінниця|Миколаїв|Суми|Полтава|'
    r'Чернівці|Черкаси|Херсон|Хмельницький|Рівне|Луцьк|Тернопіль|Ужгород|'
    r'Кропивницький|Житомир|Чернігів|Івано-Франківськ)'
    r'\s*[–—\-]\s*(20[0-2]\d|19[89]\d)',
    re.UNICODE,
)

# Рік після слова "рік" або "р.": "2023 р.", "2023 рік"
_ANCHOR_RIK = re.compile(
    r'\b(20[0-2]\d|19[89]\d)\s+(?:р(?:ік|\.)|рр\.)',
    re.UNICODE,
)

# Рік у дужках наприкінці рядка: "(2023)" — типово для титульної сторінки
_ANCHOR_PARENS_EOL = re.compile(
    r'\(\s*(20[0-2]\d|19[89]\d)\s*\)\s*$',
)

# Загальний шаблон року для фолбеку
_YEAR_GENERIC = re.compile(r'\b(20[0-2]\d|19[89]\d)\b')


def extract_dissertation_year(lines: list[dict], max_lines: int = 60) -> int | None:
    """
    Шукає рік написання дисертації в перших max_lines рядках.
    lines: list[dict] з ключами "line" та "page" — стандартна структура проєкту.
    Повертає int або None.

    Алгоритм:
      Прохід 1 — якірні фрази (пріоритет від специфічного до загального):
        а) "Місто – РРРР"     (назва міста + тире + рік)
        б) "РРРР р." / "РРРР рік"
        в) "(РРРР)" наприкінці рядка
      Прохід 2 — фолбек: max() серед усіх знайдених років,
        виключаючи майбутні роки відносно поточної дати.
    """
    current_year = datetime.datetime.now().year

    # Прохід 1a: місто + тире + рік
    for item in lines[:max_lines]:
        m = _ANCHOR_CITY_YEAR.search(item["line"])
        if m:
            return int(m.group(1))

    # Прохід 1b: РРРР р. / РРРР рік
    for item in lines[:max_lines]:
        m = _ANCHOR_RIK.search(item["line"])
        if m:
            return int(m.group(1))

    # Прохід 1c: (РРРР) наприкінці рядка
    for item in lines[:max_lines]:
        m = _ANCHOR_PARENS_EOL.search(item["line"])
        if m:
            return int(m.group(1))

    # Прохід 2 — фолбек: max() серед не-майбутніх років
    candidates: list[int] = []
    for item in lines[:max_lines]:
        for match in _YEAR_GENERIC.finditer(item["line"]):
            y = int(match.group(1))
            if y <= current_year:
                candidates.append(y)

    return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Регулярні вирази для витягу ПІБ
# ---------------------------------------------------------------------------

# Підтримує Title Case і ALL CAPS, дефіси в подвійних прізвищах
_FULL_NAME_UA = re.compile(
    r'^([А-ЯІЇЄҐ][а-яіїєґА-ЯІЇЄҐʼ\'\-]+(?:\s+[А-ЯІЇЄҐ][а-яіїєґА-ЯІЇЄҐʼ\'\-]+){1,2})$'
)

# Слова, характерні для назв установ — відкидаємо рядки що їх містять
_INSTITUTION_WORDS = {
    "університет", "університету", "інститут", "інституту",
    "академія", "академії", "міністерство", "міністерства",
    "національний", "національна", "національного", "національної",
    "державний", "державна", "державного", "державної",
    "імені", "гончара", "факультет", "кафедра",
}

# Формат з ініціалами: "Петренко В. В." або "Петренко В.В."
_NAME_WITH_INITIALS = re.compile(
    r'\b([А-ЯІЇЄҐ][а-яіїєґА-ЯІЇЄҐʼ\'\-]{1,}\s+[А-ЯІЇЄҐ]\.\s*[А-ЯІЇЄҐ]\.)'
)


def extract_dissertation_author(lines: list[dict], max_lines: int = 80) -> str | None:
    """
    Шукає ПІБ автора дисертації в перших max_lines рядках.
    lines: list[dict] з ключами "line" та "page" — стандартна структура проєкту.

    Два незалежні проходи (пріоритет: перший знайдений виграє):

    Прохід 1 — Якір «УДК»:
      Шукає рядок що починається з "УДК", потім дивиться вгору на 1-2 рядки:
      - Варіант А: рядок i-1 містить 3 слова → повне ПІБ в одному рядку
      - Варіант Б: рядок i-1 містить 2 слова + рядок i-2 містить 1 слово
        (прізвище на окремому рядку, як у СЛУЦЬКА / ТЕТЯНА ІВАНІВНА)

    Прохід 2 — Резервний (тільки якщо прохід 1 не дав результату):
      Шукає перший рядок з рівно 3 слів що відповідає патерну ПІБ
      і не містить інституційних слів.
    """
    # ------------------------------------------------------------------
    # Прохід 1: якір «УДК»
    # ------------------------------------------------------------------
    for i, item in enumerate(lines[:max_lines]):
        text = item["line"].strip()

        if text.upper().startswith("УДК") and i > 0:
            prev = lines[i - 1]["line"].strip()

            # Варіант А: повне ПІБ в одному рядку перед УДК
            if len(prev.split()) == 3:
                return prev.title()

            # Варіант Б: розірване ПІБ (СЛУЦЬКА / ТЕТЯНА ІВАНІВНА)
            if len(prev.split()) == 2 and i >= 2:
                prev_prev = lines[i - 2]["line"].strip()
                if len(prev_prev.split()) == 1 and prev_prev.isupper():
                    return f"{prev_prev} {prev}".title()

    # ------------------------------------------------------------------
    # Прохід 2: резервний — перший рядок з 3 слів, схожий на ПІБ
    # ------------------------------------------------------------------
    for item in lines[:max_lines]:
        text = item["line"].strip()

        if len(text.split()) == 3 and _FULL_NAME_UA.fullmatch(text):
            lower = text.lower()
            # Відкидаємо службові фрази
            if "рукопису" in lower or "праця" in lower:
                continue
            # Відкидаємо назви установ
            words_lower = set(lower.split())
            if words_lower & _INSTITUTION_WORDS:
                continue
            return text.title()

    return None
