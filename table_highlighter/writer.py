"""Безпечне нанесення підсвітки на runs DOCX зі збереженням тексту й посилань."""

from __future__ import annotations

from copy import deepcopy
import math

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from table_highlighter.zones import CellZones, paragraph_text


HIGHLIGHT = {"match": "yellow", "diff": "cyan"}
# lastRenderedPageBreak — кешована позначка Word між двома текстовими runs.
# Вона не входить до видимого тексту, але її не можна втратити через підсвітку.
_ALLOWED_RUN_CHILDREN = {qn("w:rPr"), qn("w:t"), qn("w:lastRenderedPageBreak")}


def supports_highlighting(paragraph) -> bool:
    """Перевіряє, що форматований текст можна поділити без втрати OOXML-вузлів."""
    for run in paragraph._p.iter(qn("w:r")):
        if any(child.tag not in _ALLOWED_RUN_CHILDREN for child in run):
            return False
    return True


def _run_text(run) -> str:
    return "".join(element.text or "" for element in run.findall(qn("w:t")))


def _rpr(run):
    properties = run.find(qn("w:rPr"))
    if properties is None:
        properties = OxmlElement("w:rPr")
        run.insert(0, properties)
    return properties


def _set_font(run, font_name: str, font_size: int, status: str | None = None) -> None:
    properties = _rpr(run)
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        properties.append(fonts)
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{name}"), font_name)
    size = properties.find(qn("w:sz"))
    if size is None:
        size = OxmlElement("w:sz")
        properties.append(size)
    size.set(qn("w:val"), str(font_size * 2))
    highlight = properties.find(qn("w:highlight"))
    if status is None:
        if highlight is not None:
            properties.remove(highlight)
    else:
        if highlight is None:
            highlight = OxmlElement("w:highlight")
            properties.append(highlight)
        highlight.set(qn("w:val"), HIGHLIGHT[status])


def _clone_run(
    run, text: str, font_name: str, font_size: int, status: str | None, *, preserve_nontext: bool
):
    clone = deepcopy(run)
    for child in tuple(clone):
        if child.tag == qn("w:t") or (child.tag != qn("w:rPr") and not preserve_nontext):
            clone.remove(child)
    text_element = OxmlElement("w:t")
    if text[:1].isspace() or text[-1:].isspace():
        text_element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_element.text = text
    clone.append(text_element)
    _set_font(clone, font_name, font_size, status)
    return clone


def style_plain_paragraph(paragraph, font_name: str, font_size: int) -> None:
    """Нормалізує шрифт, не змінюючи структуру абзацу або посилання."""
    for run in paragraph._p.iter(qn("w:r")):
        _set_font(run, font_name, font_size)


def style_text_paragraph(
    paragraph,
    statuses: tuple[str | None, ...],
    font_name: str,
    font_size: int,
) -> None:
    """Ділить лише прості текстові runs, зберігаючи їхній XML-контейнер."""
    if not supports_highlighting(paragraph):
        raise ValueError("Абзац містить непідтримуваний Word-об'єкт.")
    visible = paragraph_text(paragraph)
    run_text = "".join(_run_text(run) for run in paragraph._p.iter(qn("w:r")))
    if visible != run_text or len(visible) != len(statuses):
        raise ValueError("Не вдалося безпечно зіставити текст абзацу з Word-runs.")

    cursor = 0
    for run in tuple(paragraph._p.iter(qn("w:r"))):
        raw = _run_text(run)
        if not raw:
            _set_font(run, font_name, font_size)
            continue
        run_statuses = statuses[cursor:cursor + len(raw)]
        cursor += len(raw)
        groups: list[tuple[str, str | None]] = []
        for char, status in zip(raw, run_statuses):
            if groups and groups[-1][1] == status:
                groups[-1] = (groups[-1][0] + char, status)
            else:
                groups.append((char, status))
        parent = run.getparent()
        position = parent.index(run)
        parent.remove(run)
        for offset, (text, status) in enumerate(groups):
            parent.insert(
                position + offset,
                _clone_run(
                    run,
                    text,
                    font_name,
                    font_size,
                    status,
                    preserve_nontext=offset == 0,
                ),
            )


def _estimate_lines(text: str, chars_per_line: float) -> int:
    return max(1, math.ceil(len(text) / chars_per_line))


def _cell_chars_per_line(cell, font_size: int) -> float:
    width_pt = (cell.width / 12700) if cell.width else 400.0
    return max(1.0, width_pt / (font_size * 0.6))


def _leading_lines(zones: CellZones, chars_per_line: float) -> int:
    total = 0
    for item in zones.paragraphs:
        if item.zone == "text":
            break
        total += _estimate_lines(paragraph_text(item.paragraph), chars_per_line)
    return total


def add_alignment_padding(left_cell, right_cell, left_zones: CellZones, right_zones: CellZones, font_size: int) -> int:
    """Додає порожні абзаци перед лівою коміркою для вирівнювання маркерів."""
    difference = _leading_lines(right_zones, _cell_chars_per_line(right_cell, font_size)) - _leading_lines(
        left_zones, _cell_chars_per_line(left_cell, font_size)
    )
    for _ in range(max(0, difference)):
        left_cell._tc.insert(1, OxmlElement("w:p"))
    return max(0, difference)
