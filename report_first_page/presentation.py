"""Спільний текст HTML і Word — PLAN_REPORT_FIRST_PAGE.md, §§3–4, 6–7."""

from datetime import date
from html import escape

from .types import AcademicPerson, ReportMetadata, ReportPage, ReportParagraph, ReportRun

MISSING = "_____"
# Користувач дозволив зменшити заголовок 21.10.2026; корпус перевірено у Word.
HEADING_SIZE_PT = 22
# Один додатковий рядок перед датою: висота рядка Calibri 14 pt у Word.
DATE_SPACE_BEFORE_PT = 17
MONTHS = (
    "січень", "лютий", "березень", "квітень", "травень", "червень",
    "липень", "серпень", "вересень", "жовтень", "листопад", "грудень",
)
EXPERT_TEXT = (
    "Перевірку провела Благодєтєлєва-Вовк Світлана Леонідівна, канд. екон. наук, "
    "доцент, голова Ради ГО ТРОН, координаторка антиплагіатної ініціативи «Дисергейт»"
)
AUTHOR_WARNING = "Перевірте відмінок ПІБ автора"


def _value(value: str | None) -> str:
    return value.strip() if value and value.strip() else MISSING


def _person_runs(person: AcademicPerson, ending: str) -> tuple[ReportRun, ...]:
    """Пунктуація належить звичайним фрагментам, повне ім'я — жирному."""
    before = (person.before_name or "").strip()
    after = (person.after_name or "").strip().rstrip(",;. ")
    if not before and not after and not (person.full_name or "").strip():
        return (ReportRun(MISSING),)
    name = _value(person.full_name).rstrip(",;. ")
    return (
        ReportRun(before + " " if before else ""),
        ReportRun(name, bold=True),
        ReportRun((", " + after if after else "") + ending),
    )


def build_report_page(metadata: ReportMetadata, *, generated_on: date) -> ReportPage:
    """Збирає текст без годинника, відмінювання або залежності від локалі."""
    def paragraph(role: str, *runs: ReportRun) -> ReportParagraph:
        return ReportParagraph(role, tuple(runs))

    day = _value(metadata.defense_day)
    if day.isdecimal():
        day = day.zfill(2)
    opponents = metadata.opponents or (AcademicPerson(),)
    paragraphs = [
        paragraph("heading", ReportRun("ЗВІТ")),
        paragraph("heading", ReportRun("результатів перевірки дисертації")),
        paragraph("heading", ReportRun(
            f"{_value(metadata.author)} на здобуття {_value(metadata.degree_phrase)} "
            f"на тему «{_value(metadata.title)}» на наявність порушень академічної доброчесності"
        )),
        paragraph("separator"),
        paragraph("label", ReportRun(
            _value(metadata.supervisor_label or "Науковий керівник / консультант").rstrip(": ") + ":",
            bold=True,
        )),
        paragraph("supervisor", *_person_runs(metadata.supervisor, ".")),
        paragraph("separator"),
        paragraph("label", ReportRun("Опоненти:", bold=True)),
    ]
    for index, person in enumerate(opponents):
        paragraphs.append(paragraph(
            "opponent", *_person_runs(person, "." if index == len(opponents) - 1 else ";")
        ))
    paragraphs.extend([
        paragraph("separator"),
        paragraph("defense", ReportRun("Захист відбувся", bold=True), ReportRun(
            f" «{day}» {_value(metadata.defense_month)} {_value(metadata.defense_year)} року "
            f"на засіданні спеціалізованої вченої ради {_value(metadata.council_code)} "
            f"{_value(metadata.institution_tail).rstrip('. ')}."
        )),
        paragraph("separator"),
        paragraph("expert", ReportRun("Перевірку провела", bold=True),
                  ReportRun(EXPERT_TEXT[len("Перевірку провела"):])),
        paragraph("date", ReportRun(
            f"Київ, {MONTHS[generated_on.month - 1]} {generated_on.year:04d}", bold=True
        )),
    ])
    return ReportPage(tuple(paragraphs))


def render_report_html(page: ReportPage) -> str:
    """Показує зміст A4; не є перевіркою пагінації Word."""
    paragraphs = []
    for paragraph in page.paragraphs:
        fragments = []
        for run in paragraph.runs:
            text = escape(run.text, quote=True)
            fragments.append(f"<strong>{text}</strong>" if run.bold else text)
        paragraphs.append(
            f'<p class="report-{paragraph.role}">{"".join(fragments)}</p>'
        )
    return """<style>
.report-first-page{box-sizing:border-box;width:210mm;max-width:100%;min-height:297mm;
padding:25.4mm;background:white;color:#000;font-family:Calibri,sans-serif;font-size:14pt;
line-height:normal;overflow-wrap:break-word;white-space:pre-wrap;margin:4px auto 24px;
border:1px solid #ebecf2;border-radius:24px;box-shadow:0 4px 24px rgba(0,0,0,0.07);}
.report-first-page p{margin:0;line-height:normal;font-family:Calibri,sans-serif;font-size:14pt;}
.report-first-page .report-heading{font-family:'Calibri Light',Calibri,sans-serif;font-size:HEADING_SIZEpt;}
.report-first-page .report-separator{height:1.15em;}
.report-first-page .report-date{text-align:center;margin-top:DATE_SPACEpt;}
@media(max-width:700px){.report-first-page{padding:24px;min-height:0;}}
</style><article class="report-first-page">""".replace("HEADING_SIZE", str(HEADING_SIZE_PT)).replace("DATE_SPACE", str(DATE_SPACE_BEFORE_PT)) + "".join(paragraphs) + "</article>"
