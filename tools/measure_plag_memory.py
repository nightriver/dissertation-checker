#!/usr/bin/env python3
"""
Замір пікової пам'яті процесу для режиму очищення звіту Plag.

Кожен запуск виконує рівно одну операцію в окремому процесі й друкує пікову
пам'ять цього процесу — без нових залежностей: Windows читає
`PeakWorkingSetSize` через `ctypes`/`GetProcessMemoryInfo`, Linux бере
`resource.getrusage(RUSAGE_SELF).ru_maxrss`.

Специфікація — PLAN_PLAG_FILTER.md, §6, §10.2, етап 9.

Режими:
  --check <url>       fetch_document + побудова SourceCheck для одного джерела;
  --baseline <звіт.pdf>  імпорт streamlit і plag_filter, parse_report і
                         render_page_png для перших п'яти аркушів.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# За прямого запуску файлу Python додає до `sys.path` каталог `tools`, а не
# корінь репозиторію. Модульний запуск цього не потребує.
if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

# Прізвище й ініціали тут — вигадані дані для одного технічного виміру
# `SourceCheck`, а не реальний автор жодної дисертації — CLAUDE.md.
_PROBE_SURNAME = "Тестовенко"
_PROBE_INITIALS = "П.П."


def _peak_working_set_bytes() -> int:
    """Пікова пам'ять поточного процесу — PLAN_PLAG_FILTER.md, §10.2, етап 9."""
    if sys.platform.startswith("win"):
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # За замовчуванням ctypes бачить GetCurrentProcess() як 32-бітний int
        # і зрізає псевдодескриптор, тому GetProcessMemoryInfo падає з
        # ERROR_INVALID_HANDLE — знайдений дефект §10.2, етап 9. Типи задаємо
        # явно.
        ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            raise OSError("GetProcessMemoryInfo не повернув успіх")
        return int(counters.PeakWorkingSetSize)

    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _run_check(url: str, tmp_dir: Path) -> None:
    """Одне джерело: `fetch_document` і побудова `SourceCheck` — §5, §6, §9."""
    from plag_filter.checker import _build_check
    from plag_filter.fetch import fetch_document
    from plag_filter.rules import author_key

    result = fetch_document(url, tmp_dir=tmp_dir)
    checked_for = author_key(_PROBE_SURNAME, _PROBE_INITIALS)
    check = _build_check(url, checked_for, result, _PROBE_SURNAME, _PROBE_INITIALS)
    print(
        f"Перевірка {url}: помилка={check.error}, автор знайдений={check.author_hit is not None}"
    )


def _run_baseline(report_path: Path) -> None:
    """Розбір звіту й п'ять аркушів PNG — §7, §8, §9."""
    import streamlit  # noqa: F401  — реалістичний відбиток пам'яті застосунку

    from plag_filter.pdf import parse_report, render_page_png

    data = report_path.read_bytes()
    report = parse_report(data)
    for page in range(min(5, report.page_count)):
        render_page_png(data, report, page, set())
    print(f"Базовий замір: {report_path.name}, {report.page_count} аркушів")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="URL", help="fetch_document + SourceCheck")
    group.add_argument("--baseline", metavar="PDF", type=Path, help="parse_report + render_page_png")
    args = parser.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory() as tmp_name:
            _run_check(args.check, Path(tmp_name))
    else:
        _run_baseline(args.baseline)

    peak_bytes = _peak_working_set_bytes()
    print(f"Пікова пам'ять процесу: {peak_bytes / (1024 * 1024):.1f} МБ")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
