#!/usr/bin/env python3
"""
Відтворюване вимірювання калькованих зворотів у PDF.

CLI є тонкою оболонкою над спільними модулями застосунку: PDF розбирає
`parser.searchdoc`, збіги й щільність рахує `search.calques`, мову
бібліографії визначає `search.language`. Специфікація — PLAN_SEARCH.md,
§20.1 (крок 8 таблиці §22).
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Sequence

# За прямого запуску файлу Python додає до `sys.path` каталог `tools`, а не
# корінь репозиторію. Модульний запуск цього не потребує.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

# Сумісний модуль `fitz`, який поки імпортує парсер, друкує попередження під
# час імпорту. Воно належить stderr і не має псувати машинний JSON у stdout.
with redirect_stdout(sys.stderr):
    from parser.searchdoc import PARSER_VERSION, parse_search_document
    from search import ALGO_VERSION
    from search.calques import DICT_VERSION, compute_metrics, density_band
    from search.language import annotate_bibliography, bibliography_language_stats
    from search.types import SearchDocument


@dataclass(frozen=True)
class SectionMeasurement:
    """Метрики одного розділу, обчислені тим самим `compute_metrics`."""

    section_id: str
    heading: str
    author_words: int
    tier1_hits: int
    tier2_hits: int
    tier3_hits: int
    tier1_density: float


@dataclass(frozen=True)
class FileMeasurement:
    """Повний детермінований результат вимірювання одного PDF."""

    file: str
    document_sha256: str
    parser_version: str
    dictionary_version: str
    algo_version: str
    n_pages: int
    author_words: int
    tier1_hits: int
    tier2_hits: int
    tier3_hits: int
    tier1_density: float
    density_band: str
    excluded_zone_hits: tuple[tuple[str, int], ...]
    bibliography_total: int
    bibliography_expected: int | None
    bibliography_coverage: float | None
    bibliography_boundary_confidence: str
    language_ru: int
    language_uk: int
    language_mixed: int
    language_unknown: int
    ru_ratio: float | None
    show_ru_percentage: bool
    language_reasons: tuple[str, ...]
    sections: tuple[SectionMeasurement, ...]


def _section_measurements(document: SearchDocument) -> tuple[SectionMeasurement, ...]:
    result: list[SectionMeasurement] = []
    for section in document.sections:
        blocks = tuple(block for block in document.blocks if block.section_id == section.section_id)
        metrics = compute_metrics(replace(document, blocks=blocks))
        result.append(
            SectionMeasurement(
                section_id=section.section_id,
                heading=section.heading,
                author_words=metrics.author_words,
                tier1_hits=metrics.tier1_hits,
                tier2_hits=metrics.tier2_hits,
                tier3_hits=metrics.tier3_hits,
                tier1_density=metrics.tier1_density,
            )
        )
    return tuple(result)


def measure_pdf_bytes(data: bytes, *, name: str = "<memory>.pdf") -> FileMeasurement:
    """Вимірює PDF-байти без побічних ефектів і прихованих запасних шляхів."""
    # Сумісний `fitz` завантажується ліниво всередині парсера й може
    # надрукувати попередження. Машинний stdout має лишатися чистим JSON.
    with redirect_stdout(sys.stderr):
        document = parse_search_document(data)
    return measure_document(document, name=name)


def measure_document(
    document: SearchDocument,
    *,
    name: str = "<memory>.pdf",
) -> FileMeasurement:
    """Вимірює вже побудований документ без повторного розбору PDF."""
    metrics = compute_metrics(document)
    bibliography = annotate_bibliography(document.bibliography)
    languages = bibliography_language_stats(
        bibliography, document.body_biblio_confidence
    )
    return FileMeasurement(
        file=name,
        document_sha256=document.document_sha256,
        parser_version=PARSER_VERSION,
        dictionary_version=DICT_VERSION,
        algo_version=ALGO_VERSION,
        n_pages=document.n_pages,
        author_words=metrics.author_words,
        tier1_hits=metrics.tier1_hits,
        tier2_hits=metrics.tier2_hits,
        tier3_hits=metrics.tier3_hits,
        tier1_density=metrics.tier1_density,
        density_band=density_band(metrics.tier1_density),
        excluded_zone_hits=tuple(
            (zone.value, count) for zone, count in metrics.excluded_zone_hits
        ),
        bibliography_total=languages.total,
        bibliography_expected=languages.expected_count,
        bibliography_coverage=languages.coverage_ratio,
        bibliography_boundary_confidence=document.body_biblio_confidence.value,
        language_ru=languages.ru,
        language_uk=languages.uk,
        language_mixed=languages.mixed,
        language_unknown=languages.unknown,
        ru_ratio=languages.ru_ratio,
        show_ru_percentage=languages.show_ru_percentage,
        language_reasons=languages.reasons,
        sections=_section_measurements(document),
    )


def measure_file(path: Path) -> FileMeasurement:
    """Читає лише PDF; текстові дампи й зовнішній `pdftotext` не підтримує."""
    if path.suffix.casefold() != ".pdf":
        raise ValueError(f"Очікується PDF-файл: {path}")
    return measure_pdf_bytes(path.read_bytes(), name=path.name)


def render_json(measurements: tuple[FileMeasurement, ...]) -> str:
    """Стабільний машинний звіт із трьома версіями верхнього рівня."""
    payload = {
        "algo_version": ALGO_VERSION,
        "dictionary_version": DICT_VERSION,
        "parser_version": PARSER_VERSION,
        "files": [asdict(item) for item in measurements],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _percent(value: float | None, *, visible: bool) -> str:
    return f"{100 * value:.1f}%" if visible and value is not None else "—"


def render_table(measurements: tuple[FileMeasurement, ...], *, verbose: bool = False) -> str:
    """Людинозчитувана таблиця; `verbose` додає секції та причини відмови."""
    lines = [
        f"PARSER_VERSION={PARSER_VERSION}",
        f"DICT_VERSION={DICT_VERSION}",
        f"ALGO_VERSION={ALGO_VERSION}",
        "",
        f"{'файл':<28}{'слів':>8}{'рів.1':>7}{'рів.2':>7}{'рів.3':>7}"
        f"{'щільн.1':>10}{'записів':>9}{'RU %':>8}",
    ]
    lines.append("-" * len(lines[-1]))
    for item in measurements:
        lines.append(
            f"{Path(item.file).stem[:27]:<28}{item.author_words:>8}"
            f"{item.tier1_hits:>7}{item.tier2_hits:>7}{item.tier3_hits:>7}"
            f"{item.tier1_density:>10.2f}{item.bibliography_total:>9}"
            f"{_percent(item.ru_ratio, visible=item.show_ru_percentage):>8}"
        )
        if verbose:
            lines.append(
                "    мови: "
                f"RU={item.language_ru} UK={item.language_uk} "
                f"MIXED={item.language_mixed} UNKNOWN={item.language_unknown}"
            )
            if item.language_reasons:
                lines.append("    відсоток приховано: " + ", ".join(item.language_reasons))
            lines.append("    за розділами (рівень 1):")
            for section in item.sections:
                lines.append(
                    f"      {section.heading[:22]:<23}{section.tier1_hits:>4}  "
                    f"({section.tier1_density:.2f}/1000, {section.author_words} слів)"
                )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        measurements = tuple(measure_file(path) for path in args.files)
    except (OSError, ValueError) as exc:
        print(f"Помилка вимірювання: {exc}", file=sys.stderr)
        return 1

    print(
        render_json(measurements)
        if args.as_json
        else render_table(measurements, verbose=args.verbose)
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
