"""Моделі вхідних даних і готового тексту — PLAN_REPORT_FIRST_PAGE.md, §6."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AcademicPerson:
    before_name: str | None = None
    full_name: str | None = None
    after_name: str | None = None


@dataclass(frozen=True)
class ReportMetadata:
    author: str | None = None
    title: str | None = None
    degree_phrase: str | None = None
    supervisor_label: str | None = None
    supervisor: AcademicPerson = field(default_factory=AcademicPerson)
    opponents: tuple[AcademicPerson, ...] = ()
    defense_day: str | None = None
    defense_month: str | None = None
    defense_year: str | None = None
    council_code: str | None = None
    institution_tail: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportRun:
    text: str
    bold: bool = False


@dataclass(frozen=True)
class ReportParagraph:
    role: str
    runs: tuple[ReportRun, ...]

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True)
class ReportPage:
    paragraphs: tuple[ReportParagraph, ...]
