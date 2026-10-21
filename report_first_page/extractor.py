"""Локальне читання автореферату — PLAN_REPORT_FIRST_PAGE.md, §§5, 7."""

import re
from datetime import date

import fitz as pymupdf

from parser.searchdoc import NoTextLayerError, parse_search_document
from search.metadata import extract_search_metadata

from .types import AcademicPerson, ReportMetadata

MAX_PDF_BYTES = 30 * 1024 * 1024
RULES_VERSION = "1"
MONTHS_GENITIVE = (
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
)
_ROLES = re.compile(
    r"(?P<supervisor>Науковий\s+(?P<role>керівник|консультант))\s*:"
    r"|(?P<opponents>Офіційні\s+опоненти)\s*:"
    r"|(?P<defense>Захист\s+відбу(?:деться|вся))", re.I,
)
_START = re.compile(r"^(?:доктор|кандидат)\b.*\b(?:наук|філософії)\b", re.I)
_QUALIFICATION = re.compile(
    r"^(?:доктор|кандидат|професор|доцент|академік|член-кореспондент|заслужений|старший\s+науковий)\b",
    re.I,
)
_WORD = r"[А-ЯЁІЇЄҐ][А-Яа-яЁёІіЇїЄєҐґ]*(?:[-'’ʼ][А-Яа-яЁёІіЇїЄєҐґ]+)*"
_NAME = re.compile(rf"^({_WORD}\s+{_WORD}\s+{_WORD}),$")
_DEGREE = re.compile(
    r"на\s+здобуття\s+((?:наукового\s+)?ступеня\s+"
    r"(?:доктора\s+філософії|(?:кандидата|доктора)\s+[^.;:]*?\bнаук))", re.I,
)
_COUNCIL = re.compile(r"\b([КД])\s*(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)\b", re.I)
_DATE = re.compile(
    r"[«\"“]?\s*(\d{1,2})\s*[»\"”]?\s*(" + "|".join(MONTHS_GENITIVE)
    + r")\s*(\d{4})(?:\s*(?:року|р\.))?", re.I,
)


class ReportInputError(ValueError):
    """Вхідний файл не підходить для локального формування звіту."""


def validate_report_upload(filename: str, size: int) -> str | None:
    if not filename.lower().endswith(".pdf"):
        return "Завантажте один автореферат у форматі PDF."
    if size > MAX_PDF_BYTES:
        return "Файл більший за 30 МБ. Завантажте менший PDF."
    if size == 0:
        return "PDF порожній. Завантажте автореферат із текстовим шаром."
    return None


def _join(text: str) -> str:
    text = re.sub(r"([-\u00ad])[^\S\n]*\n\s*", r"\1", text)
    return " ".join(text.replace("\u00ad", "").split())


def normalize_person_name(name: str) -> str:
    """Змінює лише повністю прописні частини слів, без відмінювання."""
    def part(value: str) -> str:
        letters = [c for c in value if c.isalpha()]
        return value[:1].upper() + value[1:].lower() if len(letters) >= 2 and value.isupper() else value

    return " ".join("-".join(part(p) for p in word.split("-")) for word in name.split())


def _people(block: str, warnings: list[str], label: str) -> tuple[AcademicPerson, ...]:
    lines = [" ".join(line.split()) for line in block.splitlines() if line.strip()]
    if not lines:
        return ()
    chunks: list[list[str]] = []
    for line in lines:
        if _START.match(line) or not chunks:
            chunks.append([])
        chunks[-1].append(line)
    people = []
    for index, chunk in enumerate(chunks, 1):
        name_index = next((i for i, line in enumerate(chunk)
                           if not _QUALIFICATION.match(line) and _NAME.fullmatch(line)), None)
        if name_index is None:
            warnings.append(f"{label}, запис {index}: не вдалося виділити повний ПІБ; перевірте дані.")
            people.append(AcademicPerson(before_name=_join("\n".join(chunk))))
        else:
            people.append(AcademicPerson(
                before_name=_join("\n".join(chunk[:name_index])) or None,
                full_name=normalize_person_name(chunk[name_index].removesuffix(",")),
                after_name=_join("\n".join(chunk[name_index + 1:])).rstrip(",;. ") or None,
            ))
    return tuple(people)


def _defense(block: str, warnings: list[str]) -> dict[str, str | None]:
    text = _join(re.split(r"З\s+дисертацією\s+можна|Автореферат\s+розіслан", block, maxsplit=1, flags=re.I)[0])
    dates = list(_DATE.finditer(text))
    day = month = year = None
    if len(dates) == 1:
        match = dates[0]
        day, month, year = match.groups()
        month = month.casefold()
        try:
            date(int(year), MONTHS_GENITIVE.index(month) + 1, int(day))
        except ValueError:
            warnings.append("Дата захисту некоректна; уточніть день, місяць і рік.")
        else:
            day = day.zfill(2)
    else:
        warnings.append("Не знайдено однозначної повної дати захисту; уточніть її.")
        if not dates:
            head = re.split(r"на\s+засіданні", text, maxsplit=1, flags=re.I)[0]
            found_day = re.match(r'\s*[«"“]?\s*(\d{1,2})(?!\d)', head)
            found_month = re.search(r"\b(" + "|".join(MONTHS_GENITIVE) + r")\b", head, re.I)
            found_year = re.search(r"\b(\d{4})\b", head)
            day = found_day[1].zfill(2) if found_day else None
            month = found_month[1].casefold() if found_month else None
            year = found_year[1] if found_year else None
    council = _COUNCIL.search(text)
    code = None
    tail = None
    if council:
        code = council[1].upper() + " " + ".".join(council.group(i) for i in (2, 3, 4))
        tail = text[council.end():]
    else:
        anchor = re.search(r"спеціалізованої\s+вченої\s+ради\b", text, re.I)
        if anchor:
            tail = text[anchor.end():]
    if tail is not None:
        tail = re.split(r"\bза\s+адресою\b", tail, maxsplit=1, flags=re.I)[0].strip(" ,;:.") or None
    return dict(defense_day=day, defense_month=month, defense_year=year,
                council_code=code, institution_tail=tail)


def _annotation_title(document, title: str | None, author: str | None) -> str | None:
    """Регістр лише з повного збігу теми в українській анотації."""
    if not title:
        return None
    normalized = _join(title)
    # Пошук точного повного заголовка, без порогів схожості чи зміни слів.
    surname = re.escape(author.split()[0]) if author else _WORD
    expression = re.compile(
        r"(?<!\w)" + surname + r"\s+(?:[А-ЯІЇЄҐ]\.\s*){2}(?P<title>"
        + re.escape(normalized) + r")(?!\w)", re.I,
    )
    pages: dict[int, list[str]] = {}
    for block in document.blocks:
        pages.setdefault(block.physical_page, []).append(block.raw_text)
    for blocks in pages.values():
        text = _join("\n".join(blocks))
        if re.search(r"\bАНОТАЦІ(?:Я|Ї)\b", text, re.I):
            match = expression.search(text)
            if match and not match["title"].isupper():
                return match["title"]
    return title


def extract_report_metadata(pdf_bytes: bytes) -> ReportMetadata:
    """Автор/тема — метапарсер; службові дані — text перших чотирьох аркушів."""
    error = validate_report_upload("input.pdf", len(pdf_bytes))
    if error:
        raise ReportInputError(error)
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise ReportInputError("PDF захищений паролем. Завантажте незахищену копію.")
            if not len(pdf):
                raise ReportInputError("PDF не містить аркушів.")
            service_text = "\n".join(pdf[i].get_text("text") for i in range(min(4, len(pdf))))
    except ReportInputError:
        raise
    except Exception as exc:
        raise ReportInputError("Не вдалося відкрити PDF. Файл пошкоджений або має інший формат.") from exc
    try:
        document = parse_search_document(pdf_bytes)
        metadata = extract_search_metadata(document)
    except NoTextLayerError as exc:
        raise ReportInputError("PDF не містить текстового шару. OCR у цьому модулі не виконується.") from exc
    except Exception as exc:
        raise ReportInputError("Не вдалося прочитати текст PDF. Перевірте файл.") from exc

    warnings: list[str] = []
    supervisor = AcademicPerson()
    supervisor_label = None
    opponents: tuple[AcademicPerson, ...] = ()
    defense = {}
    anchors = list(_ROLES.finditer(service_text))
    degree = _DEGREE.search(_join(service_text[:anchors[0].start()] if anchors else service_text))
    seen = set()
    for index, anchor in enumerate(anchors):
        kind = next(k for k in ("supervisor", "opponents", "defense") if anchor[k])
        if kind in seen:
            warnings.append("Повторний службовий блок: перевірте розпізнані реквізити.")
            continue
        seen.add(kind)
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(service_text)
        block = service_text[anchor.end():end]
        if kind == "supervisor":
            supervisor_label = "Науковий " + anchor["role"].casefold()
            people = _people(block, warnings, supervisor_label)
            if people:
                supervisor = people[0]
                if len(people) > 1:
                    warnings.append("У блоці супроводу декілька записів; перевірте керівника / консультанта.")
        elif kind == "opponents":
            opponents = _people(block, warnings, "Опоненти")
        else:
            defense = _defense(block, warnings)
    result = ReportMetadata(
        author=metadata.author,
        title=_annotation_title(document, metadata.title, metadata.author),
        degree_phrase=degree[1] if degree else None,
        supervisor_label=supervisor_label, supervisor=supervisor, opponents=opponents,
        **defense,
    )
    for name, value in (
        ("автора", result.author), ("тему", result.title), ("ступінь", result.degree_phrase),
        ("керівника / консультанта", result.supervisor.full_name),
        ("опонентів", result.opponents), ("дату захисту", result.defense_year),
        ("код ради", result.council_code), ("установу захисту", result.institution_tail),
    ):
        if not value:
            warnings.append(f"Не вдалося визначити {name}; уточніть дані вручну.")
    return ReportMetadata(**{**vars(result), "warnings": tuple(warnings)})
