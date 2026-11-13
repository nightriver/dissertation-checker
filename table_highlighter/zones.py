"""Виділення службових і порівнюваних зон у комірках таблиці DOCX."""

from __future__ import annotations

import re
from dataclasses import dataclass

from docx.oxml.ns import qn


PAGE_MARKER_RE = re.compile(r"^\s*[СсCc]\.\s*\d+[\d\s\-–—]*\s*$")
COMMENT_RE = re.compile(r"^(?:\d+\.|Посилання на:|Там само$)")
NUMBERED_RE = re.compile(r"^\d+\.")


@dataclass(frozen=True)
class ParagraphZone:
    """Абзац комірки та його роль у поточному рядку."""

    paragraph: object
    index: int
    zone: str


@dataclass(frozen=True)
class CellZones:
    """Повна розмітка комірки; усі абзаци присутні в результаті."""

    paragraphs: tuple[ParagraphZone, ...]
    marker_index: int | None
    warning: str | None = None

    @property
    def text_paragraphs(self) -> tuple[ParagraphZone, ...]:
        return tuple(item for item in self.paragraphs if item.zone == "text")


# Нетекстові вузли run із фіксованим видимим текстом. М'який перенос
# невидимий і слова не розриває, тому дає порожній рядок.
_INLINE_TEXT = {
    qn("w:tab"): "\t",
    qn("w:br"): "\n",
    qn("w:cr"): "\n",
    qn("w:noBreakHyphen"): "-",
    qn("w:softHyphen"): "",
}
INLINE_TAGS = frozenset({*_INLINE_TEXT, qn("w:sym")})


def element_text(element) -> str | None:
    """Видимий текст дочірнього вузла run; None — вузол тексту не несе."""
    if element.tag == qn("w:t"):
        return element.text or ""
    if element.tag == qn("w:sym"):
        # Символьні шрифти Word зсувають код на 0xF000: F02D у Symbol — це «-».
        try:
            code = int(element.get(qn("w:char"), ""), 16)
        except ValueError:
            return ""
        code = code - 0xF000 if code >= 0xF000 else code
        return chr(code) if code else ""
    return _INLINE_TEXT.get(element.tag)


def paragraph_text(paragraph) -> str:
    """Повертає видимий текст, включно з текстом усередині гіперпосилань."""
    parts: list[str] = []
    for element in paragraph._p.iter():
        # w:tab трапляється й у w:pPr/w:tabs як позиція табуляції — це не текст.
        if element.getparent() is None or element.getparent().tag != qn("w:r"):
            continue
        text = element_text(element)
        if text is not None:
            parts.append(text)
    return "".join(parts)


def is_page_marker(text: str) -> bool:
    """Розпізнає маркер сторінки порівнюваного фрагмента."""
    return bool(PAGE_MARKER_RE.match(text.strip()) or text.strip() == "--")


def annotate_left_cell(cell) -> CellZones:
    """Текст ліворуч розташований після першого маркера до першого пробілу."""
    paragraphs = tuple(cell.paragraphs)
    marker_index = next(
        (index for index, paragraph in enumerate(paragraphs) if is_page_marker(paragraph_text(paragraph))),
        None,
    )
    if marker_index is None:
        return CellZones(
            tuple(ParagraphZone(paragraph, index, "plain") for index, paragraph in enumerate(paragraphs)),
            None,
            "Не знайдено маркер сторінки в лівій комірці.",
        )

    text_open = True
    text_seen = False
    result: list[ParagraphZone] = []
    for index, paragraph in enumerate(paragraphs):
        text = paragraph_text(paragraph)
        if index <= marker_index or is_page_marker(text):
            zone = "plain"
        elif text_open and not text.strip():
            # Порожні абзаци між маркером і текстом зону ще не закривають.
            text_open = not text_seen
            zone = "plain"
        else:
            zone = "text" if text_open else "plain"
            text_seen = text_seen or text_open
        result.append(ParagraphZone(paragraph, index, zone))
    return CellZones(tuple(result), marker_index)


def annotate_right_cell(cell) -> CellZones:
    """Текст праворуч іде після першого маркера до коментаря.

    Джерело може складатися з кількох уривків, кожен зі своєю сторінкою
    («с. 87», текст, «с. 89», текст). Наступні маркери лишаються службовими
    абзацами всередині зони, а вирівнюється перший — навпроти маркера ліворуч.
    """
    paragraphs = tuple(cell.paragraphs)
    marker_index = next(
        (index for index, paragraph in enumerate(paragraphs) if is_page_marker(paragraph_text(paragraph))),
        None,
    )
    if marker_index is None:
        return CellZones(
            tuple(ParagraphZone(paragraph, index, "plain") for index, paragraph in enumerate(paragraphs)),
            None,
            "Не знайдено маркер сторінки в правій комірці.",
        )

    text_open = True
    text_seen = False
    result: list[ParagraphZone] = []
    for index, paragraph in enumerate(paragraphs):
        text = paragraph_text(paragraph).strip()
        # Нумерований пункт одразу після маркера — це сам текст джерела
        # («4. Спеціальний режим…»), а не номер у переліку посилань.
        numbered_source = not text_seen and NUMBERED_RE.match(text)
        if index <= marker_index or is_page_marker(text):
            zone = "plain"
        elif text_open and COMMENT_RE.match(text) and not numbered_source:
            text_open = False
            zone = "plain"
        else:
            zone = "text" if text_open else "plain"
            text_seen = text_seen or (text_open and bool(text))
        result.append(ParagraphZone(paragraph, index, zone))
    return CellZones(tuple(result), marker_index)
