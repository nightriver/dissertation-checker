"""
extractor.py
Витяг тексту з PDF і DOCX у вигляді списку рядків.
Кожен елемент: {"line": str, "page": int | None}
"""

from __future__ import annotations
import re
import datetime

from parser.types import LineItem


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


def extract_lines_from_pdf(data: bytes) -> list[LineItem]:
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

    result: list[LineItem] = []
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


# ---------------------------------------------------------------------------
# Автонумеровані списки Word
# ---------------------------------------------------------------------------
# У DOCX номер елемента нумерованого списку НЕ зберігається в тексті параграфа —
# Word обчислює його під час рендерингу з numbering.xml. python-docx віддає
# лише `para.text`, тому бібліографія, оформлена як список Word, приходить
# зовсім без номерів і parse_bibliography не бачить жодного запису.
#
# Тут ми відтворюємо нумерацію: для кожної пари (numId, ilvl) ведемо лічильник
# і підставляємо номер у текст у форматі «N. », який очікує _ENTRY_START.

# Формати numFmt, які дають число. Маркери (bullet) і літерні/римські формати
# нумерації джерел не використовуються — їх не чіпаємо.
_NUMERIC_FMTS = {"decimal", "decimalZero", "ordinal"}

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _numbering_start_and_format(doc, num_id: int, ilvl: int) -> tuple[int, str]:
    """
    Повертає (start, numFmt) для рівня ilvl списку num_id з numbering.xml.
    Якщо визначення недоступне — (1, "decimal"), тобто звичайна нумерація з 1.
    """
    try:
        numbering = doc.part.numbering_part.element
    except (AttributeError, KeyError, ValueError):
        return 1, "decimal"

    ns = _W_NS
    abstract_id = None
    for num in numbering.findall(f"{ns}num"):
        if num.get(f"{ns}numId") == str(num_id):
            ref = num.find(f"{ns}abstractNumId")
            if ref is not None:
                abstract_id = ref.get(f"{ns}val")
            break
    if abstract_id is None:
        return 1, "decimal"

    for abstract in numbering.findall(f"{ns}abstractNum"):
        if abstract.get(f"{ns}abstractNumId") != abstract_id:
            continue
        for lvl in abstract.findall(f"{ns}lvl"):
            if lvl.get(f"{ns}ilvl") != str(ilvl):
                continue
            start_el = lvl.find(f"{ns}start")
            fmt_el = lvl.find(f"{ns}numFmt")
            start = int(start_el.get(f"{ns}val")) if start_el is not None else 1
            fmt = fmt_el.get(f"{ns}val") if fmt_el is not None else "decimal"
            return start, fmt
    return 1, "decimal"


def _style_num_ref(style, depth: int = 0) -> tuple[int, int] | None:
    """
    (numId, ilvl) зі СТИЛЮ параграфа, з урахуванням ланцюжка basedOn.

    Нумерація може бути оголошена не на самому параграфі, а на його стилі
    (типово для вбудованого «List Number»). Без цієї гілки такі списки
    залишаються без номерів.
    """
    if style is None or depth > 10:      # захист від циклу basedOn
        return None

    element = getattr(style, "element", None)
    if element is None:
        return None

    pPr = element.find(f"{_W_NS}pPr")
    if pPr is not None:
        num_pr = pPr.find(f"{_W_NS}numPr")
        if num_pr is not None:
            num_id_el = num_pr.find(f"{_W_NS}numId")
            if num_id_el is not None and num_id_el.get(f"{_W_NS}val") is not None:
                ilvl_el = num_pr.find(f"{_W_NS}ilvl")
                ilvl = int(ilvl_el.get(f"{_W_NS}val")) if ilvl_el is not None else 0
                return int(num_id_el.get(f"{_W_NS}val")), ilvl

    return _style_num_ref(getattr(style, "base_style", None), depth + 1)


def _para_num_ref(para) -> tuple[int, int] | None:
    """(numId, ilvl) параграфа, якщо він належить автонумерованому списку."""
    pPr = para._p.pPr
    if pPr is not None and pPr.numPr is not None:
        num_pr = pPr.numPr
        if num_pr.numId is not None and num_pr.numId.val is not None:
            ilvl = (num_pr.ilvl.val
                    if num_pr.ilvl is not None and num_pr.ilvl.val is not None
                    else 0)
            return int(num_pr.numId.val), int(ilvl)

    # Пряме оголошення на параграфі відсутнє — дивимося на стиль.
    return _style_num_ref(getattr(para, "style", None))


def extract_lines_from_docx(data: bytes) -> list[LineItem]:
    """
    Повертає список {"line": str, "page": None} для кожного непорожнього
    параграфа DOCX. Номер сторінки недоступний без рендерингу.

    Номери елементів автонумерованих списків Word відновлюються й підставляються
    в текст — інакше бібліографія, оформлена списком, втрачає всі номери.
    Заголовки (стиль Heading) не нумеруються, щоб не зламати розпізнавання
    заголовка списку літератури.
    """
    _check_size(data)

    import io

    try:
        from docx import Document
    except ImportError as e:
        raise ImportError("Бібліотека python-docx не встановлена.") from e

    doc = Document(io.BytesIO(data))
    result: list[LineItem] = []

    counters: dict[tuple[int, int], int] = {}
    level_meta: dict[tuple[int, int], tuple[int, str]] = {}

    for para in doc.paragraphs:
        stripped = para.text.rstrip()
        if not stripped:
            continue

        style_name = para.style.name if para.style else ""
        ref = None if "heading" in style_name.lower() else _para_num_ref(para)

        if ref is not None:
            if ref not in level_meta:
                level_meta[ref] = _numbering_start_and_format(doc, ref[0], ref[1])
            start, fmt = level_meta[ref]
            if fmt in _NUMERIC_FMTS:
                counters[ref] = counters.get(ref, start - 1) + 1
                stripped = f"{counters[ref]}. {stripped.lstrip()}"

        result.append({"line": stripped, "page": None})

    return result


def extract_lines(data: bytes, filename: str) -> list[LineItem]:
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

# Міста, де відбуваються захисти дисертацій. Спільна константа для двох
# патернів нижче: «Місто – РРРР» в одному рядку і «Місто» окремим рядком.
_CITIES = (
    'Київ|Харків|Львів|Одеса|Дніпро|Запоріжжя|Вінниця|Миколаїв|Суми|Полтава|'
    'Чернівці|Черкаси|Херсон|Хмельницький|Рівне|Луцьк|Тернопіль|Ужгород|'
    'Кропивницький|Житомир|Чернігів|Івано-Франківськ|'
    'Острог|Умань|Переяслав|Дрогобич|Біла Церква|Маріуполь|Краматорськ|'
    "Кам['’ʼ]янець-Подільський|Слов['’ʼ]янськ"
)

# Рік після назви міста з тире: "Київ – 2023", "Харків — 2021"
_ANCHOR_CITY_YEAR = re.compile(
    rf'(?:{_CITIES})\s*[–—\-]\s*(20[0-2]\d|19[89]\d)',
    re.UNICODE,
)

# Рядок, що складається лише з назви міста — типова титульна сторінка PDF,
# де «Київ» і «2023» опиняються в різних рядках.
_CITY_ONLY = re.compile(rf'^\s*(?:{_CITIES})\s*[–—\-,]?\s*$', re.UNICODE)

# Рядок, що складається лише з року: "2023", "2023 р.", "2023 рік".
_BARE_YEAR = re.compile(r'^\s*(20[0-2]\d|19[89]\d)\s*(?:р(?:ік|\.)?)?\s*$')

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


def extract_dissertation_year(lines: list[LineItem], max_lines: int = 60) -> int | None:
    """
    Шукає рік написання дисертації в перших max_lines рядках.
    lines: list[dict] з ключами "line" та "page" — стандартна структура проєкту.
    Повертає int або None.

    Алгоритм:
      Прохід 1 — якірні фрази (пріоритет від специфічного до загального):
        а)   "Місто – РРРР"   (назва міста + тире + рік в одному рядку)
        а-біс) "Місто" окремим рядком, рік — у наступному або через один
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

    # Прохід 1a-біс: місто окремим рядком, рік — у наступному або через один
    window = lines[:max_lines]
    for i, item in enumerate(window):
        if not _CITY_ONLY.match(item["line"]):
            continue
        for nxt in window[i + 1:i + 3]:
            m = _BARE_YEAR.match(nxt["line"])
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

# Апострофи, які трапляються в українських іменах: Вʼячеслав, Дар’я, Лук'ян.
_APOSTROPHES = "'’ʼ‘`"


def _title_ua(text: str) -> str:
    """
    Регістр «Як Ім'я» з урахуванням української орфографії.

    str.title() вважає апостроф межею слова й дає «Дар’Я», «Вʼячеслав» →
    «ВʼЯчеслав». Тут велика літера ставиться лише після пробілу та дефіса
    (дефіс потрібен для подвійних прізвищ: «Іванов-Петренко»).
    """
    out: list[str] = []
    cap_next = True
    for ch in text:
        if ch.isspace() or ch == "-":
            cap_next = True
            out.append(ch)
        elif cap_next and ch.isalpha():
            out.append(ch.upper())
            cap_next = False
        else:
            out.append(ch.lower())
    return "".join(out)


def _looks_like_person_name(text: str) -> bool:
    """
    True якщо рядок схожий на ПІБ, а не на назву установи чи службову фразу.

    Застосовується в ОБОХ проходах extract_dissertation_author. Раніше ця
    перевірка була тільки в резервному проході, тому якір «УДК» повертав
    як автора будь-який рядок із трьох слів — зокрема «Міністерство освіти
    України» та «На правах рукопису», які стоять на титулці майже завжди.
    """
    if not _FULL_NAME_UA.fullmatch(text):
        return False
    lower = text.lower()
    if "рукопису" in lower or "праця" in lower:
        return False
    return not (set(lower.split()) & _INSTITUTION_WORDS)


def extract_dissertation_author(lines: list[LineItem], max_lines: int = 80) -> str | None:
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
            if len(prev.split()) == 3 and _looks_like_person_name(prev):
                return _title_ua(prev)

            # Варіант Б: розірване ПІБ (СЛУЦЬКА / ТЕТЯНА ІВАНІВНА)
            if len(prev.split()) == 2 and i >= 2:
                prev_prev = lines[i - 2]["line"].strip()
                if len(prev_prev.split()) == 1 and prev_prev.isupper():
                    candidate = f"{prev_prev} {prev}"
                    if _looks_like_person_name(candidate):
                        return _title_ua(candidate)
            # Якщо кандидат не пройшов перевірку — не виходимо, а шукаємо далі:
            # управління дійде до резервного проходу нижче.

    # ------------------------------------------------------------------
    # Прохід 2: резервний — перший рядок з 3 слів, схожий на ПІБ
    # ------------------------------------------------------------------
    for item in lines[:max_lines]:
        text = item["line"].strip()

        if len(text.split()) == 3 and _looks_like_person_name(text):
            return _title_ua(text)

    return None
