"""Підсумковий PDF нового формату: дисертація з підсвіткою джерел, що
залишилися, і протокол — PLAN_PLAG_FILTER_V4.md, §6.5, §7 етап 6.

Звіт Plag нового формату як документ не очищується (§1.1): підсумок — копія
дисертації, де острови кожного невиключеного джерела позначено анотацією
highlight кольору джерела, з підписом «№N · домен». Протокол додається в
кінець тим самим `pdf.append_protocol`, що й у старому форматі.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from plag_filter.pdf import append_protocol
from plag_filter.project import protocol_paragraphs
from plag_filter.types import PlagProject, PlagReport
from plag_filter.v4.page import island_page_spans, line_boxes

# «Фон фрагмента» палітри Plag — PLAN_PLAG_VIEW.md, §5; індекс `(номер − 1) % 10`.
PALETTE_BACKGROUND: tuple[tuple[int, int, int], ...] = (
    (248, 187, 208),
    (209, 196, 233),
    (187, 222, 251),
    (178, 235, 242),
    (178, 223, 219),
    (200, 230, 201),
    (240, 244, 195),
    (255, 236, 179),
    (255, 204, 188),
    (215, 204, 200),
)


def _color(number: int) -> tuple[float, float, float]:
    r, g, b = PALETTE_BACKGROUND[(number - 1) % len(PALETTE_BACKGROUND)]
    return (r / 255, g / 255, b / 255)


def highlighted_pdf_name(diss_name: str, report: PlagReport) -> str:
    """Ім'я підсумкового файлу — §6.5: `<основа імені дисертації>_plag_<report_id>.pdf`."""
    report_id = report.v4.report.report_id or "report"
    return f"{Path(diss_name).stem}_plag_{report_id}.pdf"


def highlighted_dissertation(report: PlagReport, project: PlagProject, diss_data: bytes) -> bytes:
    """Дисертація з підсвіткою невиключених джерел і протоколом — §6.5.

    Анотація — одна на острів і аркуш (острів, що переходить на інший аркуш,
    дає по анотації на кожен).
    """
    doc = fitz.open(stream=diss_data, filetype="pdf")
    try:
        for number in sorted(report.rows):
            state = project.states.get(number)
            if state is not None and state.decision == "exclude":
                continue
            title = f"№{number} · {report.rows[number].label}"
            color = _color(number)
            for island in report.v4.islands.get(number, ()):
                for page_index, start, end in island_page_spans(report, island.start, island.end):
                    rects = [fitz.Rect(box) for box in line_boxes(report, start, end)]
                    # Посилання на аркуш тримається, поки жива анотація: інакше
                    # PyMuPDF звільняє аркуш і анотація «не прив'язана».
                    page = doc[page_index]
                    annot = page.add_highlight_annot(rects)
                    annot.set_colors(stroke=color)
                    annot.set_info(title=title, content=report.rows[number].urls[0])
                    annot.update()
        highlighted = doc.tobytes()
    finally:
        doc.close()
    return append_protocol(highlighted, protocol_paragraphs(project, report))
