#!/usr/bin/env python3
"""
Наскрізний прогін режиму очищення звіту Plag поза браузером.

Вбудований браузер `AppTest` не вміє завантажувати файли, тому приймання
етапу 9 замінює його цим скриптом: `parse_report` → `new_project` →
підтвердження автора й року → `check_batch` партіями по 20 до вичерпання
(справжня мережа) → одне ручне рішення → `to_json` → `from_json` →
`recompute` → `filter_pdf` + `append_protocol` → `<ім'я>_filtered.pdf`.

Специфікація — PLAN_PLAG_FILTER.md, §10.2, етап 9. Прізвище та ініціали
автора приймальник передає аргументами командного рядка — у файли
репозиторію вони не потрапляють.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# За прямого запуску файлу Python додає до `sys.path` каталог `tools`, а не
# корінь репозиторію. Модульний запуск цього не потребує.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from plag_filter.checker import check_batch
from plag_filter.fetch import fetch_document
from plag_filter.pdf import append_protocol, filter_pdf, parse_report, render_page_png
from plag_filter.project import from_json, new_project, protocol_paragraphs, to_json
from plag_filter.rules import recompute
from plag_filter.types import PlagProject, PlagReport, REASON_LABELS

_TMP_PREFIX = "plag_fetch_"

# Лог завантажень поточної партії: (адреса, тривалість с, розміри байт, код помилки).
_DownloadLogEntry = tuple[str, float, tuple[int, ...], str | None]


@contextmanager
def _track_downloaded_sizes(tmp_dir: Path) -> Iterator[list[int]]:
    """Перехопити розміри тимчасових файлів `fetch_document` перед видаленням.

    `fetch_document` (§6) сам видаляє тимчасовий файл у `finally` й не
    повертає розмір завантаженого документа. Для звіту приймання (§10.2,
    етап 9) і підбору найбільших документів для `measure_plag_memory.py`
    розмір потрібен, тож скрипт тимчасово перехоплює видалення саме цих
    файлів — і тільки на час одного виклику `fetch`.
    """
    sizes: list[int] = []
    original_unlink = pathlib.Path.unlink

    def patched_unlink(self: pathlib.Path, missing_ok: bool = False) -> None:
        try:
            if self.parent == tmp_dir and self.name.startswith(_TMP_PREFIX):
                sizes.append(self.stat().st_size)
        except OSError:
            pass
        original_unlink(self, missing_ok=missing_ok)

    pathlib.Path.unlink = patched_unlink  # type: ignore[assignment]
    try:
        yield sizes
    finally:
        pathlib.Path.unlink = original_unlink  # type: ignore[assignment]


def _make_timed_fetch(tmp_dir: Path, log: list[_DownloadLogEntry]):
    """Обгортка над `fetch_document`, що записує час і розмір у `log` — §9."""

    def timed_fetch(url: str, *, tmp_dir: Path = tmp_dir):
        start = time.perf_counter()
        with _track_downloaded_sizes(tmp_dir) as sizes:
            result = fetch_document(url, tmp_dir=tmp_dir)
        elapsed = time.perf_counter() - start
        log.append((url, elapsed, tuple(sizes), result.error))
        return result

    return timed_fetch


def _print_batch_log(batch_no: int, elapsed: float, checked: list[int], log: list[_DownloadLogEntry]) -> None:
    print(f"Партія {batch_no}: {len(checked)} джерел, {elapsed:.1f} с")
    for url, dt, sizes, error in log:
        size_text = ", ".join(f"{size:,} байт".replace(",", " ") for size in sizes) if sizes else "н/д"
        print(f"  {url}: {dt:.2f} с; розмір: {size_text}; помилка: {error or '-'}")


def _pick_manual_number(project: PlagProject) -> int | None:
    """Обрати номер для єдиного ручного рішення §10.2 — детерміновано.

    Перевага — спірному джерелу (щоб рішення мало видимий ефект), інакше
    перше джерело за номером.
    """
    disputed = sorted(
        number for number, state in project.states.items() if state.decision == "disputed"
    )
    if disputed:
        return disputed[0]
    numbers = sorted(project.states)
    return numbers[0] if numbers else None


def _pick_demo_page(report: PlagReport, excluded: set[int]) -> int | None:
    """Аркуш тіла, де є і виключені, і залишені джерела — для PNG до/після §10.2."""
    for page in range(report.body_first, report.list_first):
        numbers = report.numbers_by_page.get(page, ())
        has_excluded = any(number in excluded for number in numbers)
        has_kept = any(number not in excluded for number in numbers)
        if has_excluded and has_kept:
            return page
    return None


def _print_counters(project: PlagProject, report: PlagReport) -> None:
    counted = sum(
        1
        for number, row in report.rows.items()
        if row.percent is None or row.percent >= 0.1
    )
    print(f"Джерел ≥ 0,1 % (або нерозпізнаний відсоток): {counted}")

    by_reason: Counter[str] = Counter(state.reason for state in project.states.values())
    for reason, label in REASON_LABELS.items():
        print(f"  {label}: {by_reason.get(reason, 0)}")

    errors: Counter[str] = Counter()
    for state in project.states.values():
        if state.check is not None and state.check.error is not None:
            errors[state.check.error] += 1
    if errors:
        print("Недоступні за кодами помилок:")
        for code, count in sorted(errors.items()):
            print(f"  {code}: {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="PDF звіту Plag")
    parser.add_argument("--surname", required=True)
    parser.add_argument("--initials", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path, help="каталог поза репозиторієм")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    data = args.report.read_bytes()

    report = parse_report(data)
    project = new_project(report, args.report.name)
    project.surname = args.surname
    project.initials = args.initials
    project.year = args.year
    project.confirmed = True
    recompute(project, report)

    with tempfile.TemporaryDirectory(dir=args.out) as tmp_name:
        tmp_dir = Path(tmp_name)
        batch_no = 0
        total_batch_seconds = 0.0
        while True:
            batch_no += 1
            log: list[_DownloadLogEntry] = []
            fetch = _make_timed_fetch(tmp_dir, log)
            start = time.perf_counter()
            checked = check_batch(report, project, fetch=fetch, tmp_dir=tmp_dir, limit=20)
            elapsed = time.perf_counter() - start
            total_batch_seconds += elapsed
            _print_batch_log(batch_no, elapsed, checked, log)
            if not checked:
                break
        print(f"Загальний час усіх партій: {total_batch_seconds:.1f} с")

    manual_number = _pick_manual_number(project)
    if manual_number is not None:
        project.states[manual_number].manual = "keep"
        recompute(project, report)
        print(f"Ручне рішення: №{manual_number} → залишити")

    serialized = to_json(project)
    project = from_json(serialized, report)
    recompute(project, report)

    project_path = args.out / f"{args.report.stem}_project.json"
    project_path.write_text(to_json(project), encoding="utf-8")

    excluded = {number for number, state in project.states.items() if state.decision == "exclude"}
    filtered = filter_pdf(data, report, excluded)
    filtered = append_protocol(filtered, protocol_paragraphs(project, report))
    out_pdf = args.out / f"{args.report.stem}_filtered.pdf"
    out_pdf.write_bytes(filtered)

    demo_page = _pick_demo_page(report, excluded)
    if demo_page is not None:
        (args.out / "page_before.png").write_bytes(render_page_png(data, report, demo_page, set()))
        (args.out / "page_after.png").write_bytes(render_page_png(data, report, demo_page, excluded))
        print(f"PNG до/після збережено для аркуша {demo_page + 1} (з 1)")
    else:
        print("Не знайдено аркуша із сумішшю виключених і залишених джерел для PNG")

    _print_counters(project, report)
    print(f"Проєкт: {project_path}")
    print(f"Очищений PDF: {out_pdf}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
