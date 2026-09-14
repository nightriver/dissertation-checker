#!/usr/bin/env python3
"""
Вибірка для ручного розмічання дат публікацій режиму очищення звіту Plag.

Виконує повну перевірку звіту (справжня мережа), обирає до `--count` джерел
із причинами `date_unknown`, `later`, `same_year` з найбільшою вагою W,
повторно завантажує кожне і зберігає текст та збіги `citation_years` для
ручного розмічання оркестратором — PLAN_PLAG_FILTER_V2.md, §9.2, етап 4.

`--check <json>` звіряє рішення `later` з ручною розміткою: ложне `later` —
коли рішення `later`, а розмітка каже, що документ не написано пізніше за
рік дисертації (`later_proof` хибне або `date_year` не більший за рік).

Прізвище та ініціали автора — лише з `extract_author(title_text)`, у файли
репозиторію не потрапляють.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

# За прямого запуску файлу Python додає до `sys.path` каталог `tools`, а не
# корінь репозиторію. Модульний запуск цього не потребує.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from plag_filter.checker import check_batch
from plag_filter.fetch import fetch_document
from plag_filter.pdf import parse_report
from plag_filter.project import new_project
from plag_filter.rules import (
    _CITATION_YEAR_PATTERNS,
    citation_years,
    derive_initials,
    extract_author,
    recompute,
)
from plag_filter.types import PlagProject, PlagReport, REASON_LABELS

_SAMPLE_REASONS = ("date_unknown", "later", "same_year")
_CONTEXT_CHARS = 40


def _select_sample(project: PlagProject, report: PlagReport, count: int) -> list[int]:
    """Обрати до `count` номерів причин вибірки за спаданням W — §9.2 етап 4."""
    candidates = [
        number for number, state in project.states.items() if state.reason in _SAMPLE_REASONS
    ]
    candidates.sort(key=lambda number: (-report.highlight_width.get(number, 0.0), number))
    return candidates[:count]


def _citation_matches_with_context(pages: list[str]) -> list[str]:
    """Усі збіги `citation_years` із 40 символами контексту — §9.2 етап 4."""
    lines: list[str] = []
    for page_index, text in enumerate(pages):
        normalized = text.casefold()
        found: list[tuple[int, int, int]] = []
        for pattern in _CITATION_YEAR_PATTERNS:
            for match in pattern.finditer(normalized):
                found.append((match.start(), match.end(), int(match.group(1))))
        found.sort(key=lambda item: item[0])
        for start, end, year in found:
            ctx_start = max(0, start - _CONTEXT_CHARS)
            ctx_end = min(len(normalized), end + _CONTEXT_CHARS)
            context = normalized[ctx_start:ctx_end].replace("\n", " ")
            lines.append(f"стор. {page_index + 1}: {year} — «{context}»")
    return lines


def _write_sample_file(
    out_dir: Path, number: int, url: str, project: PlagProject, tmp_dir: Path
) -> tuple[int | None, str | None, int]:
    """Файл `<номер>.txt` для ручного розмічання — §9.2 етап 4."""
    state = project.states[number]
    check = state.check
    date_year = check.doc_date.start.year if check is not None and check.doc_date else None
    date_basis = check.date_basis if check is not None else None

    result = fetch_document(url, tmp_dir=tmp_dir)
    lines = [
        f"Номер: {number}",
        f"Адреса: {url}",
        f"Причина: {REASON_LABELS.get(state.reason, state.reason)}",
        f"Дата: {date_year if date_year is not None else '—'}; основа: {date_basis or '—'}",
        "",
    ]
    citation_over_year = 0
    if result.ok:
        pages = result.pages[:2] if result.kind == "pdf" else result.pages
        head_text = "".join(pages)[:3000]
        lines.append("Текст (перші 3000 символів, для PDF — сторінки 1–2):")
        lines.append(head_text)
        lines.append("")
        lines.append("Збіги citation_years:")
        lines.extend(_citation_matches_with_context(result.pages))
        years = citation_years(result.pages)
        if project.year is not None:
            citation_over_year = sum(1 for year in years if year > project.year)
    else:
        lines.append(f"Завантаження не вдалося: {result.error}")

    (out_dir / f"{number}.txt").write_text("\n".join(lines), encoding="utf-8")
    return date_year, date_basis, citation_over_year


def _run_label_check(project: PlagProject, report: PlagReport, check_path: Path) -> None:
    """Звірити рішення `later` з ручною розміткою §9.2 етап 4."""
    data = json.loads(check_path.read_text(encoding="utf-8"))
    record = data.get(report.sha256)
    if record is None:
        print(f"У {check_path} немає розмітки для звіту {report.sha256}")
        return

    dissertation_year = record.get("year")
    items = record.get("items", {})
    false_later = 0
    matches = 0
    mismatches: list[int] = []
    for number_text, expectation in items.items():
        number = int(number_text)
        state = project.states.get(number)
        if state is None or state.reason != "later":
            continue
        date_year = expectation.get("date_year")
        later_proof = expectation.get("later_proof")
        is_false = later_proof is False or (
            date_year is not None and dissertation_year is not None and date_year <= dissertation_year
        )
        if is_false:
            false_later += 1
            mismatches.append(number)
        else:
            matches += 1

    print(f"Ложних later: {false_later}")
    print(f"Підтверджених later: {matches}")
    if mismatches:
        print(f"Розбіжності (номери): {', '.join(str(n) for n in sorted(mismatches))}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="PDF звіту Plag")
    parser.add_argument(
        "--auto-author",
        action="store_true",
        help="взяти ПІБ із extract_author(title_text) — обов'язково",
    )
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path, help="каталог поза репозиторієм")
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--check", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.auto_author:
        parser.error("--auto-author обов'язковий")

    args.out.mkdir(parents=True, exist_ok=True)
    data = args.report.read_bytes()
    report = parse_report(data)

    guess = extract_author(report.title_text)
    if guess is None:
        print("Автора не знайдено в титулі.")
        return 2
    initials = derive_initials(guess.given_name, guess.patronymic)
    print(
        f"Автор: {guess.surname} {guess.given_name} {guess.patronymic} ({initials}), "
        f"confidence={guess.confidence}"
    )

    project = new_project(report, args.report.name)
    project.surname = guess.surname
    project.given_name = guess.given_name
    project.patronymic = guess.patronymic
    project.initials = initials
    project.year = args.year
    project.confirmed = True
    recompute(project, report)

    summary_rows: list[tuple[int, str, str, int | None, str | None, int]] = []
    with tempfile.TemporaryDirectory(dir=args.out) as tmp_name:
        tmp_dir = Path(tmp_name)
        while True:
            checked = check_batch(report, project, tmp_dir=tmp_dir, limit=20, workers=args.workers)
            if not checked:
                break

        sample = _select_sample(project, report, args.count)
        for number in sample:
            row = report.rows[number]
            state = project.states[number]
            url = state.alt_url if state.alt_url else row.urls[0]
            date_year, date_basis, citation_over = _write_sample_file(args.out, number, url, project, tmp_dir)
            summary_rows.append((number, url, state.reason, date_year, date_basis, citation_over))

    summary_path = args.out / "summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["номер", "адреса", "причина", "рік дати", "основа", "цитувань пізніше року"])
        writer.writerows(summary_rows)

    print(f"Вибірка: {len(summary_rows)} джерел, {summary_path}")

    if args.check is not None:
        _run_label_check(project, report, args.check)

    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
