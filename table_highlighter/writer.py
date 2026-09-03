"""Безпечне нанесення підсвітки на runs DOCX зі збереженням тексту й посилань."""

from __future__ import annotations

from copy import deepcopy

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from table_highlighter.formatting import set_run_font
from table_highlighter.zones import paragraph_text


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


def _set_font(run, font_name: str, font_size: int, status: str | None = None) -> None:
    set_run_font(run, font_name, font_size, HIGHLIGHT.get(status))


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
